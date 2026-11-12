import SwiftUI
import UIKit

// The front door and the layers over the whole app: the welcome screen and
// its rotating samples (§181), the FAM intro (§201), the log-out splash,
// the Terms card (§228), the update screen (§208) and the waitlist's status
// page (decision 2). Every word is the web's (static/index.html,
// static/waitlist.html) or the server's.

/// A retired build (426): the server's sentence and its store link.
struct UpdateNotice: Equatable, Identifiable {
    let message: String
    /// `update_url` (APP_STORE_URL); "" when the server has none.
    let link: String
    var id: String { message + "|" + link }

    /// `client_versions.status_for`'s words, for an answer that had none.
    static let fallback = "This version of FAM is no longer supported. Update FAM to keep listening."
}

// MARK: - The front door

/// Signed out: the welcome screen, then Sign up (the waitlist join) or Log
/// in. The welcome screen is not on a stack: Back goes to it by name
/// (`intro-not-on-stack`, `backToWelcome`).
struct FrontDoorView: View {
    enum Door: Hashable { case welcome, signUp, logIn }

    @State private var door: Door = .welcome

    var body: some View {
        ZStack {
            switch door {
            case .welcome:
                WelcomeView(signUp: { go(.signUp) }, logIn: { go(.logIn) })
                    .transition(Self.step)
            case .signUp:
                AuthView(making: true, back: { go(.welcome) })
                    .transition(Self.step)
            case .logIn:
                AuthView(making: false, back: { go(.welcome) })
                    .transition(Self.step)
            }
        }
        .background(FAMColor.background.ignoresSafeArea())
    }

    /// The words slide 24 pt and fade between steps (`Motion` "step").
    static let step = AnyTransition.asymmetric(
        insertion: AnyTransition.offset(x: 24).combined(with: .opacity),
        removal: AnyTransition.offset(x: -24).combined(with: .opacity))

    private func go(_ next: Door) {
        withAnimation(.easeOut(duration: 0.26)) { door = next }
    }
}

/// `screen-welcome`: the wordmark, "Listen to anything you want to know
/// about", today's most-played samples turning over, and Sign Up / Log In
/// docked at the foot. "Continue as guest" stays withdrawn (§197).
struct WelcomeView: View {
    let signUp: () -> Void
    let logIn: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Wordmark(size: 30)
                        .frame(maxWidth: .infinity)
                        .padding(.bottom, 18)
                    Text("Listen to anything you want to know about")
                        .font(FAMFont.heading(20, .semibold))
                        .foregroundStyle(FAMColor.text)
                        .fixedSize(horizontal: false, vertical: true)
                    SampleCarousel()
                        .padding(.vertical, 22)
                }
                .padding(.horizontal, 22)
                .padding(.top, 34)
            }
            VStack(spacing: 11) {
                Button("Sign Up", action: signUp)
                    .buttonStyle(EntryButtonStyle(primary: true))
                Button("Log In", action: logIn)
                    .buttonStyle(EntryButtonStyle(primary: false))
            }
            .padding(.horizontal, 22)
            .padding(.bottom, 24)
        }
        .background(FAMColor.background.ignoresSafeArea())
    }
}

/// `.entry-btn`: 46 tall, Geist 13.5, copper and bold when primary,
/// outlined otherwise.
struct EntryButtonStyle: ButtonStyle {
    let primary: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(FAMFont.body(13.5, primary ? .bold : .semibold))
            .foregroundStyle(primary ? FAMColor.ink : FAMColor.text)
            .frame(maxWidth: .infinity, minHeight: 46)
            .background(primary ? FAMColor.copper : Color.clear, in: Capsule())
            .overlay(Capsule().stroke(primary ? FAMColor.copper : FAMColor.borderStrong))
            .opacity(configuration.isPressed ? 0.85 : 1)
    }
}

// MARK: - The samples (`loadWelcomeSamples`, the status page's card)

/// Today's three most-played episodes, one at a time, turning every 4.5 s
/// with a 260 ms fade - never away from the one being listened to - with
/// its dots. A tap plays or pauses it in place, as a replay: no player, no
/// navigation, "Starting the episode…" until the first word and the
/// server's sentence if it fails. Hidden, not empty, when there are none.
struct SampleCarousel: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer

    @State private var samples: [Tile] = []
    @State private var at = 0
    @State private var fading = false

    static let turnEvery: UInt64 = 4_500_000_000

    var body: some View {
        Group {
            if !samples.isEmpty {
                content
            }
        }
        .task { await load() }
        .task(id: samples.count) { await rotate() }
        .onChange(of: player.phase) { _, phase in
            if phase == .playing { model.sampleStarted() }
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Most played today")
                .font(FAMFont.mono(10.5))
                .tracking(0.84)
                .textCase(.uppercase)
                .foregroundStyle(FAMColor.textMuted)
                .padding(.bottom, 8)
            card(samples[min(at, samples.count - 1)])
                .opacity(fading ? 0 : 1)
            if samples.count > 1 { dots.padding(.top, 9) }
            if !model.sampleNote.isEmpty {
                Text(model.sampleNote)
                    .font(FAMFont.body(12))
                    .foregroundStyle(FAMColor.textMuted)
                    .frame(maxWidth: .infinity)
                    .multilineTextAlignment(.center)
                    .padding(.top, 6)
            }
        }
    }

    private func isPlaying(_ tile: Tile) -> Bool {
        model.sampleQuery == tile.query && player.playing
    }

    private func card(_ tile: Tile) -> some View {
        let playing = isPlaying(tile)
        let title = tile.title.isEmpty ? tile.query : tile.title
        return Button { model.toggleSample(tile) } label: {
            VStack(alignment: .leading, spacing: 0) {
                picture(tile, playing: playing)
                VStack(alignment: .leading, spacing: 0) {
                    Text(title)
                        .font(FAMFont.heading(15, .semibold))
                        .foregroundStyle(FAMColor.text)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    if !tile.hook.isEmpty {
                        Text(tile.hook)
                            .font(FAMFont.body(12))
                            .foregroundStyle(FAMColor.textMuted)
                            .lineSpacing(2)
                            .multilineTextAlignment(.leading)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 6)
                    }
                }
                .padding(.horizontal, 11)
                .padding(.top, 10)
                .padding(.bottom, 12)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13).stroke(FAMColor.border))
            .clipShape(RoundedRectangle(cornerRadius: 13))
        }
        .buttonStyle(.press)
        .accessibilityLabel((playing ? "Pause " : "Play ") + title)
    }

    /// The rail's picture at 16:9, the drawing under it, the facet on its
    /// cream label and the copper play / pause.
    private func picture(_ tile: Tile, playing: Bool) -> some View {
        let url = model.api.picture(tile.thumb)
        return ZStack(alignment: .bottomLeading) {
            if let url {
                Picture(url: url)
            } else {
                Rectangle().fill(FAMColor.thumb)
                SeedThumb(icon: tile.icon)
            }
            if !tile.tagText.isEmpty {
                Text(tile.tagText)
                    .font(FAMFont.mono(8))
                    .tracking(0.8)
                    .textCase(.uppercase)
                    .foregroundStyle(Color(hex: 0x2A2530))
                    .padding(.horizontal, 6)
                    .padding(.top, 3)
                    .padding(.bottom, 2)
                    .background(Color(hex: 0xEDE6D6).opacity(0.92), in: RoundedRectangle(cornerRadius: 4))
                    .padding(.leading, 9)
                    .padding(.bottom, 8)
            }
        }
        .aspectRatio(16.0 / 9.0, contentMode: .fit)
        .frame(maxWidth: .infinity)
        .clipped()
        .overlay(alignment: .bottomTrailing) {
            Image(systemName: playing ? "pause.fill" : "play.fill")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(FAMColor.ink)
                .frame(width: 34, height: 34)
                .background(FAMColor.copper, in: Circle())
                .padding(.trailing, 10)
                .padding(.bottom, 9)
        }
    }

    private var dots: some View {
        HStack(spacing: 6) {
            ForEach(0..<samples.count, id: \.self) { i in
                Button { turn(to: i) } label: {
                    Circle()
                        .fill(i == at ? FAMColor.copper : FAMColor.borderStrong)
                        .frame(width: 6, height: 6)
                        .padding(5)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Sample \(i + 1)")
            }
        }
        .frame(maxWidth: .infinity)
    }

    private func load() async {
        let found = (try? await model.api.samples()) ?? []
        samples = Array(found.prefix(3))
        // Back with a sample still playing: stay on it.
        at = max(0, samples.firstIndex { $0.query == model.sampleQuery } ?? 0)
    }

    private func rotate() async {
        guard samples.count > 1 else { return }
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: Self.turnEvery)
            if Task.isCancelled { return }
            // Never turn away from the one being listened to.
            if model.sampleQuery.isEmpty { turn(to: at + 1) }
        }
    }

    private func turn(to index: Int) {
        guard !samples.isEmpty else { return }
        withAnimation(.easeInOut(duration: 0.26)) { fading = true }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.26) {
            let count = max(1, samples.count)
            at = ((index % count) + count) % count
            withAnimation(.easeInOut(duration: 0.26)) { fading = false }
        }
    }
}

// MARK: - The FAM intro (§201, `playEntryHandoff`)

/// Four beats, about 1.8 s, over a screen that is already there (a picture
/// only - it takes no touches): the wordmark in the middle, the F and M
/// fold into the A (260 ms in, 420 ms), the chevron charges copper to
/// yellow in two pulses (from 700 ms, 560 ms), and the page slides off to
/// the left with the mark going with it (1320 ms, 480 ms). Skipped under
/// Reduce Motion, as on the web. The web's panel holds a copy of the page
/// being left; here it is the page's own background (the closest picture a
/// SwiftUI overlay can draw), which is exactly what the web shows on opening.
struct IntroLayer: View {
    let run: Int
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var shown = false
    @State private var folded = false
    @State private var charged = false
    @State private var slid = false

    static let chargeAt = 0.7

    var body: some View {
        // The mark sits where the opening's wordmark sits (centred in the
        // safe area), so the opening hands over without a jump; the panel
        // reaches the edges.
        GeometryReader { geo in
            if shown {
                ZStack {
                    FAMColor.background
                        .shadow(color: Color.black.opacity(0.35), radius: 16, x: 12)
                        .ignoresSafeArea()
                        .offset(x: slid ? -(geo.size.width + 40) : 0)
                    IntroMark(folded: folded, charged: charged)
                        .offset(x: slid ? -26 : 0)
                        .opacity(slid ? 0 : 1)
                }
                .frame(width: geo.size.width, height: geo.size.height)
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
        .onChange(of: run) { _, _ in play() }
    }

    private func play() {
        // Already playing: a second ask goes straight in.
        guard !shown, !reduceMotion else { return }
        folded = false
        charged = false
        slid = false
        shown = true
        after(0.26) {
            withAnimation(.timingCurve(0.6, 0, 0.3, 1, duration: 0.42)) { folded = true }
        }
        // Two pulses to the bright yellow, holding there (30 / 45 / 75 %).
        after(Self.chargeAt) { withAnimation(.easeInOut(duration: 0.168)) { charged = true } }
        after(Self.chargeAt + 0.168) { withAnimation(.easeInOut(duration: 0.084)) { charged = false } }
        after(Self.chargeAt + 0.252) { withAnimation(.easeInOut(duration: 0.168)) { charged = true } }
        after(1.32) {
            withAnimation(.timingCurve(0.55, 0, 0.35, 1, duration: 0.48)) { slid = true }
        }
        after(1.82) { shown = false }
    }

    private func after(_ seconds: Double, _ work: @escaping () -> Void) {
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }
}

/// The wordmark with each letter its own: F and M fold into the A (to its
/// centre, at 0.4, fading), and the A grows to 1.8 as the mark.
struct IntroMark: View {
    let folded: Bool
    let charged: Bool
    /// The splash's 30 pt wordmark (`Wordmark(size: 30)`), so the opening
    /// hands over without a jump.
    var capHeight: CGFloat = 30 * 0.68

    var body: some View {
        let c = capHeight
        let line = c * 2.9 / 16.9
        let style = StrokeStyle(lineWidth: line, lineCap: .round, lineJoin: .round)
        let fWidth = c * 12.9 / 16.9
        let mWidth = c * 14.9 / 16.9
        // From each letter's centre to the A's.
        let toAFromF = fWidth / 2 + c * 0.13 + c / 2
        let toAFromM = c / 2 + c * 0.22 + mWidth / 2
        let aColor = charged ? FAMColor.deeper : FAMColor.copper
        return HStack(spacing: 0) {
            Glyph(path: FAMLetters.f, box: CGRect(x: 4.55, y: 4.55, width: 12.9, height: 16.9))
                .stroke(FAMColor.copper, style: style)
                .frame(width: fWidth, height: c)
                .scaleEffect(folded ? 0.4 : 1)
                .offset(x: folded ? toAFromF : 0)
                .opacity(folded ? 0 : 1)
            Glyph(path: FAMLetters.a, box: CGRect(x: 3.55, y: 4.55, width: 16.9, height: 16.9))
                .stroke(aColor, style: style)
                .frame(width: c, height: c)
                .scaleEffect(folded ? 1.8 : 1)
                .padding(.leading, c * 0.13)
                .padding(.trailing, c * 0.22)
            Glyph(path: FAMLetters.m, box: CGRect(x: 4.55, y: 4.55, width: 14.9, height: 16.9))
                .stroke(FAMColor.copper, style: style)
                .frame(width: mWidth, height: c)
                .scaleEffect(folded ? 0.4 : 1)
                .offset(x: folded ? -toAFromM : 0)
                .opacity(folded ? 0 : 1)
        }
    }
}

/// The log-out splash: the FAM mark over everything (`showSplash`), faded
/// out over .35 s.
struct SplashLayer: View {
    let shown: Bool

    var body: some View {
        ZStack {
            if shown {
                FAMColor.background
                    .ignoresSafeArea()
                    .overlay { Wordmark(size: 30) }
                    .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.35), value: shown)
    }
}

// MARK: - The Terms, agreed (§228, `#termsOverlay`)

/// Over everything, with no way past but Continue: "We've updated our
/// Terms" for an account that agreed to an older version, "One thing
/// before you go on" for one that never did. Continue waits for the tick.
struct TermsLayer: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ZStack {
            if let owed = model.termsOwed {
                OverlayModal(close: {}) {
                    TermsCard(owed: owed)
                }
                .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.2), value: model.termsOwed)
    }
}

struct TermsCard: View {
    @EnvironmentObject private var model: AppModel
    let owed: TermsState
    @State private var agreed = false
    @State private var working = false

    var body: some View {
        ModalTitle(text: owed.acceptedVersion != nil ? "We\u{2019}ve updated our Terms"
                                                     : "One thing before you go on")
        Text("To keep using your FAM account, please agree to FAM\u{2019}s terms.")
            .font(FAMFont.body(12.5))
            .foregroundStyle(FAMColor.textMuted)
            .lineSpacing(5)
            .fixedSize(horizontal: false, vertical: true)
        Button { agreed.toggle() } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: agreed ? "checkmark.square.fill" : "square")
                    .font(.system(size: 17))
                    .foregroundStyle(agreed ? FAMColor.copper : FAMColor.borderStrong)
                TermsSentence()
            }
        }
        .buttonStyle(.press)
        .accessibilityAddTraits(agreed ? .isSelected : [])
        .padding(.top, 14)
        HStack {
            Spacer()
            ModalButton(label: "Continue", primary: true, enabled: agreed && !working) {
                working = true
                Task {
                    await model.acceptTerms()
                    working = false
                }
            }
        }
        .padding(.top, 16)
    }
}

// MARK: - The update screen (426, `clientStatusSeen`)

/// A retired build: nothing else would work, so this is all there is - the
/// server's sentence and the store link it sent. With no link, the App
/// Store app itself is opened (the closest place an iPhone updates from).
struct UpdateRequiredView: View {
    let notice: UpdateNotice

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            Wordmark(size: 30).padding(.bottom, 26)
            Text("Update FAM")
                .font(FAMFont.heading(20, .semibold))
                .foregroundStyle(FAMColor.text)
            Text(notice.message)
                .font(FAMFont.body(13.5))
                .foregroundStyle(FAMColor.textMuted)
                .multilineTextAlignment(.center)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 12)
            Spacer()
            Button("Update FAM") { openStore() }
                .buttonStyle(EntryButtonStyle(primary: true))
                .padding(.bottom, 24)
        }
        .padding(.horizontal, 28)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(FAMColor.background.ignoresSafeArea())
    }

    private func openStore() {
        let link = notice.link.isEmpty ? "itms-apps://apps.apple.com/" : notice.link
        if let url = URL(string: link) { UIApplication.shared.open(url) }
    }
}

// MARK: - The waitlist's status page (`/waitlist/me`, decision 2)

/// "<First>, you're a / Founding FAM member · #place", today's samples,
/// the place card (or "You're in." with Open FAM), "Move up the list" with
/// the link to copy and share, Your FAM with its invite slots and the
/// friend search, and the menu: Edit profile, Sign out, Delete account.
struct WaitlistStatusPage: View {
    @EnvironmentObject private var model: AppModel

    @State private var page: WaitlistMe?
    @State private var problem = ""
    @State private var menu = false
    @State private var editing = false
    @State private var confirmDelete = false
    @State private var confirmSignOut = false
    @State private var share: [Any]?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                top
                Button { openAbout() } label: {
                    HStack(spacing: 6) {
                        Text("Learn more about FAM")
                        Image(systemName: "arrow.up.right").font(.system(size: 10, weight: .bold))
                    }
                    .font(FAMFont.body(12.5, .semibold))
                    .foregroundStyle(FAMColor.copper)
                }
                .buttonStyle(.press)
                .padding(.top, 14)
                hello.padding(.top, 18)
                // An account with no password (§296's email alone) sets one
                // here (§571, `#passwordCard`): the web's order, above the
                // samples and the place.
                if page?.details?.hasPassword == false {
                    WaitlistPasswordCard { await load() }.padding(.top, 18)
                }
                SampleCarousel().padding(.top, 18)
                if let page {
                    placeOrGranted(page).padding(.top, 18)
                    shareCard.padding(.top, 14)
                    WaitlistFamSection(page: page, invite: { invite() }, copy: { copyLink() },
                                       reload: { await load() })
                        .padding(.top, 22)
                }
                if !problem.isEmpty {
                    Text(problem)
                        .font(FAMFont.body(12.5))
                        .foregroundStyle(FAMColor.coral)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 14)
                }
                // iOS's own: ask again now rather than reopening the app.
                Button("Check again") { Task { await model.open() } }
                    .buttonStyle(OutlineButtonStyle())
                    .frame(maxWidth: .infinity)
                    .padding(.top, 26)
            }
            .padding(.horizontal, 22)
            .padding(.bottom, 30)
        }
        .refreshable { await load() }
        .background(FAMColor.background.ignoresSafeArea())
        .task { await load() }
        .confirmationDialog("", isPresented: $menu, titleVisibility: .hidden) {
            Button("Edit profile") { DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { editing = true } }
            Button("Sign out") { signOut() }
            Button("Delete account", role: .destructive) {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { confirmDelete = true }
            }
            Button("Cancel", role: .cancel) {}
        }
        .alert("Delete your FAM account?", isPresented: $confirmDelete) {
            Button("Delete", role: .destructive) { deleteAccount() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Your place in line, your profile and your invites go with it. This can\u{2019}t be undone.")
        }
        .alert("Sign out?", isPresented: $confirmSignOut) {
            Button("Sign out") { Task { await model.signOut() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("You have no password yet, so you couldn\u{2019}t sign back in. Set one on this page first. Sign out anyway?")
        }
        .screenCover(isPresented: $editing) {
            IdentityView(mode: .edit)
                .famObjects(model)
                .famThemed()
        }
        .sheet(isPresented: Binding(get: { share != nil }, set: { if !$0 { share = nil } })) {
            ActivityView(items: share ?? []) { share = nil }
        }
        .onChange(of: editing) { _, open in
            if !open { Task { await load() } }
        }
    }

    private var top: some View {
        HStack {
            Wordmark(size: 26)
            Spacer()
            Button { menu = true } label: {
                Image(systemName: "gearshape")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 40, height: 40)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            .accessibilityLabel("Settings")
        }
        .padding(.top, 14)
    }

    /// `drawPlace`'s greeting.
    private var hello: some View {
        let first = (page?.name ?? "").split(separator: " ").first.map(String.init) ?? ""
        let place = page?.place ?? 0
        let granted = page?.status == "active"
        let tag = (place > 0 && !granted) ? " \u{00B7} #" + Self.count(place) : ""
        return VStack(alignment: .leading, spacing: 2) {
            Text(first.isEmpty ? "You\u{2019}re a" : first + ", you\u{2019}re a")
                .font(FAMFont.heading(22, .medium))
                .foregroundStyle(FAMColor.textMuted)
            Text("Founding FAM member" + tag)
                .font(FAMFont.heading(26, .semibold))
                .foregroundStyle(FAMColor.text)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private func placeOrGranted(_ page: WaitlistMe) -> some View {
        if page.status == "active" {
            WaitlistCard {
                Text("You\u{2019}re in.")
                    .font(FAMFont.heading(30, .semibold))
                    .foregroundStyle(FAMColor.text)
                Text("Your profile and your friends are already there.")
                    .font(FAMFont.body(13))
                    .foregroundStyle(FAMColor.textMuted)
                    .padding(.top, 6)
                Button("Open FAM") { Task { await model.open() } }
                    .buttonStyle(EntryButtonStyle(primary: true))
                    .padding(.top, 16)
            }
        } else {
            WaitlistPlaceCard(page: page)
        }
    }

    private var shareCard: some View {
        WaitlistCard {
            Text("Move up the list")
                .font(FAMFont.heading(18, .semibold))
                .foregroundStyle(FAMColor.text)
            Text("Share your link. Every person who joins with it moves you up.")
                .font(FAMFont.body(13))
                .foregroundStyle(FAMColor.textMuted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 6)
            HStack(spacing: 8) {
                Text(link)
                    .font(FAMFont.body(12.5))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
                    .padding(.horizontal, 12)
                    .frame(maxWidth: .infinity, minHeight: 40, alignment: .leading)
                    .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
                    .accessibilityLabel("Your invite link")
                Button("Copy") { copyLink() }
                    .buttonStyle(OutlineButtonStyle())
            }
            .padding(.top, 12)
            Button { invite() } label: {
                HStack(spacing: 8) {
                    Text("Share your link")
                    Image(systemName: "square.and.arrow.up").font(.system(size: 13, weight: .bold))
                }
            }
            .buttonStyle(EntryButtonStyle(primary: true))
            .padding(.top, 12)
        }
    }

    /// The referral link, never relative (`link()`).
    private var link: String {
        let raw = page?.referralLink ?? ""
        if raw.hasPrefix("http") { return raw }
        return model.api.base.absoluteString + raw
    }

    private func copyLink() {
        UIPasteboard.general.string = link
        model.nowPlaying.say("Invite link copied")
    }

    /// `invite()`: the share sheet with the web's words and the link.
    private func invite() {
        var items: [Any] = ["Come get early access to FAM with me."]
        if let url = URL(string: link) { items.append(url) }
        share = items
    }

    private func load() async {
        do {
            page = try await model.api.waitlistMe()
            problem = ""
        } catch FAMError.signedOut {
            await model.open()
        } catch {
            problem = SocialModel.sentence(error, else: "Something went wrong. Try again.")
        }
    }

    /// Joined with the email alone (§296): this phone is the only way in
    /// until a password is set (the card above, §571), so signing out is
    /// said first.
    private func signOut() {
        if page?.details?.hasPassword == false {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { confirmSignOut = true }
            return
        }
        Task { await model.signOut() }
    }

    private func deleteAccount() {
        Task {
            do {
                try await model.deleteAccount()
            } catch {
                model.nowPlaying.say(SocialModel.sentence(error, else: "Could not delete that"))
            }
        }
    }

    private func openAbout() {
        let base = model.api.base.absoluteString
        if let url = URL(string: base + "/waitlist#about") { UIApplication.shared.open(url) }
    }

    static func count(_ n: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.locale = Locale(identifier: "en_US")
        return f.string(from: NSNumber(value: n)) ?? String(n)
    }
}

/// "Set a password so you can sign back in" (§571, the web's
/// `#passwordCard`): drawn while `/api/waitlist/me` says the account has no
/// password, it sets the first with `/api/auth/password/set` - the way back
/// in from any device where "Forgot password?" needs mail this server may
/// not have. The app's own rule and sentences; the log-in's show/hide eye.
struct WaitlistPasswordCard: View {
    @EnvironmentObject private var model: AppModel
    /// Reload the page, which then draws no card.
    let done: () async -> Void

    @State private var password = ""
    @State private var showPassword = false
    @State private var working = false
    @State private var problem = ""

    var body: some View {
        WaitlistCard {
            Text("Set a password so you can sign back in")
                .font(FAMFont.heading(18, .semibold))
                .foregroundStyle(FAMColor.text)
                .fixedSize(horizontal: false, vertical: true)
            Text("You joined with just your email. Choose a password and you can sign in from any device.")
                .font(FAMFont.body(13))
                .foregroundStyle(FAMColor.textMuted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 6)
            HStack {
                Group {
                    if showPassword {
                        TextField("", text: $password,
                                  prompt: Text("Choose a password (at least 8 characters)")
                                    .foregroundColor(FAMColor.textFaint))
                    } else {
                        SecureField("", text: $password,
                                    prompt: Text("Choose a password (at least 8 characters)")
                                        .foregroundColor(FAMColor.textFaint))
                    }
                }
                .textContentType(.newPassword)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.go)
                .onSubmit { Task { await set() } }
                Button { showPassword.toggle() } label: {
                    Image(systemName: showPassword ? "eye.slash" : "eye")
                        .foregroundStyle(FAMColor.textMuted)
                }
                .accessibilityLabel(showPassword ? "Hide password" : "Show password")
                .accessibilityAddTraits(showPassword ? .isSelected : [])
            }
            .modifier(FieldBox())
            .padding(.top, 12)
            if !problem.isEmpty {
                Text(problem)
                    .font(FAMFont.body(12.5))
                    .foregroundStyle(FAMColor.coral)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 10)
            }
            Button {
                Task { await set() }
            } label: {
                Text(working ? "Saving\u{2026}" : "Set password")
            }
            .buttonStyle(EntryButtonStyle(primary: true))
            .disabled(working)
            .padding(.top, 12)
        }
    }

    private func set() async {
        guard !working else { return }
        guard !password.isEmpty else {
            problem = "Enter your password."
            return
        }
        guard password.count >= 8 else {
            problem = "Use at least 8 characters for your password."
            return
        }
        guard password.count <= 1024 else {
            problem = "That password is too long."
            return
        }
        problem = ""
        working = true
        defer { working = false }
        do {
            try await model.api.setPassword(password)
            password = ""
            showPassword = false
            model.nowPlaying.say("Password set. Sign in with it on any device.")
            await done()
        } catch {
            problem = SocialModel.sentence(error, else: "That did not work")
        }
    }
}

/// The status page's `.card`.
struct WaitlistCard<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            content()
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(FAMColor.border))
    }
}

/// `#placeCard`: the exact place, the biggest thing on the page, its
/// sentence, and the You → Full access rail with its five ticks.
struct WaitlistPlaceCard: View {
    let page: WaitlistMe

    var body: some View {
        WaitlistCard {
            HStack(alignment: .center, spacing: 14) {
                Text(placeText)
                    .font(FAMFont.heading(40, .bold))
                    .foregroundStyle(FAMColor.copper)
                    .minimumScaleFactor(0.6)
                    .lineLimit(1)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Your place on the waitlist").monoLabel(9)
                    Text(sentence)
                        .font(FAMFont.body(13))
                        .foregroundStyle(FAMColor.text)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            rail.padding(.top, 18)
            Text("Invite friends to move up faster.")
                .font(FAMFont.body(12))
                .foregroundStyle(FAMColor.textMuted)
                .padding(.top, 10)
        }
    }

    private var placeText: String {
        let place = page.place ?? 0
        return place > 0 ? "#" + WaitlistStatusPage.count(place) : "\u{2014}"
    }

    /// `drawPlace`'s three sentences.
    private var sentence: String {
        if page.inNextBatch == true { return "You\u{2019}re in the next batch. Keep an eye on your inbox." }
        if (page.cutoff ?? 0) > 0, let until = page.placesUntil {
            return WaitlistStatusPage.count(until) + (until == 1 ? " place" : " places") + " until full access."
        }
        let total = page.total ?? 0
        return total > 0 ? "Out of " + WaitlistStatusPage.count(total) + " on the list." : "You\u{2019}re moving up the list."
    }

    /// How much of the line in front of them is already inside the cutoff.
    private var share: Double {
        if page.inNextBatch == true { return 1 }
        let place = Double(page.place ?? 0)
        let cutoff = Double(page.cutoff ?? 0)
        guard place > 0, cutoff > 0 else { return 0 }
        return min(1, cutoff / place)
    }

    private var rail: some View {
        VStack(spacing: 6) {
            HStack {
                Text("You").monoLabel(9)
                Spacer()
                Text("Full access").monoLabel(9)
            }
            GeometryReader { geo in
                railBody(width: geo.size.width)
            }
            .frame(height: 14)
        }
        .accessibilityHidden(true)
    }

    private func railBody(width: CGFloat) -> some View {
        let inner = max(0, width - 24)
        let frac = CGFloat(share)
        return ZStack(alignment: .leading) {
            Capsule().fill(FAMColor.border).frame(height: 4).padding(.horizontal, 12)
            Capsule().fill(FAMColor.copper).frame(width: inner * frac, height: 4).padding(.leading, 12)
            ForEach(1...5, id: \.self) { i in
                Circle()
                    .fill(CGFloat(i) / 6 <= frac ? FAMColor.copper : FAMColor.borderStrong)
                    .frame(width: 6, height: 6)
                    .offset(x: 12 + inner * CGFloat(i) / 6 - 3)
            }
            Circle()
                .fill(FAMColor.copper)
                .frame(width: 14, height: 14)
                .overlay(Circle().stroke(FAMColor.background, lineWidth: 2))
                .offset(x: 12 + inner * min(frac, 0.97) - 7)
            Circle()
                .stroke(FAMColor.copper, lineWidth: 2)
                .frame(width: 10, height: 10)
                .offset(x: width - 12 - 5)
        }
    }
}

/// "Your FAM": the friend search while on the list (by name or handle, or
/// an email or number typed whole; nobody found is the invite card), then
/// the faces - invitees first - with "in FAM" under anybody already in,
/// and at least three empty slots that invite.
struct WaitlistFamSection: View {
    @EnvironmentObject private var model: AppModel
    let page: WaitlistMe
    let invite: () -> Void
    let copy: () -> Void
    let reload: () async -> Void

    @State private var words = ""
    @State private var found: [WaitlistPerson] = []
    @State private var searched = ""
    @State private var adding: Set<String> = []

    private var searching: Bool { page.admin == true || page.status != "active" }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Your FAM").monoLabel(9)
                Spacer()
                // The web's Share here copies the link (`copyLink`).
                Button { copy() } label: {
                    HStack(spacing: 5) {
                        Image(systemName: "link").font(.system(size: 11, weight: .bold))
                        Text("Share")
                    }
                    .font(FAMFont.mono(10))
                    .foregroundStyle(FAMColor.text)
                }
                .buttonStyle(.press)
            }
            if searching { searchField.padding(.top, 12) }
            if searching && !searched.isEmpty { results.padding(.top, 10) }
            faces.padding(.top, 14)
        }
        .task(id: words) { await search() }
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(FAMColor.textFaint)
            TextField("", text: $words,
                      prompt: Text("Search friends on the waitlist...").foregroundColor(FAMColor.textFaint))
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(FAMFont.body(13))
                .foregroundStyle(FAMColor.text)
                .accessibilityLabel("Search friends on the waitlist by name, email or phone number")
        }
        .padding(.horizontal, 12)
        .frame(minHeight: 40)
        .background(FAMColor.surface, in: Capsule())
        .overlay(Capsule().stroke(FAMColor.borderStrong))
    }

    private var results: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(found) { person in
                resultRow(person)
            }
            inviteCard
        }
    }

    private func resultRow(_ p: WaitlistPerson) -> some View {
        let done = p.added || p.friend
        let label = p.friend ? "Friends" : (p.added ? "Added" : "Add")
        return HStack(spacing: 10) {
            Face(name: p.name, handle: p.handle, avatar: p.avatar, size: 36)
            VStack(alignment: .leading, spacing: 2) {
                Text(p.name.isEmpty ? "@" + p.handle : p.name)
                    .font(FAMFont.body(13, .bold))
                    .foregroundStyle(FAMColor.text)
                Text(p.waitlisted ? "On the waitlist" : "In FAM")
                    .font(FAMFont.body(11.5))
                    .foregroundStyle(FAMColor.textMuted)
            }
            Spacer(minLength: 6)
            ModalButton(label: label, primary: !done, enabled: !done && !adding.contains(p.userId)) {
                add(p)
            }
        }
        .padding(10)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
    }

    private var inviteCard: some View {
        HStack(spacing: 10) {
            Image(systemName: "person.2")
                .font(.system(size: 15))
                .foregroundStyle(FAMColor.copper)
                .frame(width: 36, height: 36)
            Text("Can\u{2019}t find your friend on the waitlist? Invite them and move up the waitlist!")
                .font(FAMFont.body(12))
                .foregroundStyle(FAMColor.textMuted)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 6)
            ModalButton(label: "Invite", primary: true) { invite() }
        }
        .padding(10)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
    }

    private var faces: some View {
        let people = page.fam ?? []
        let empty = max(3, 5 - people.count)
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: 14) {
                ForEach(Array(people.enumerated()), id: \.offset) { _, p in
                    famFace(p)
                }
                ForEach(0..<empty, id: \.self) { _ in
                    Button { invite() } label: {
                        Image(systemName: "plus")
                            .font(.system(size: 16, weight: .medium))
                            .foregroundStyle(FAMColor.textMuted)
                            .frame(width: 52, height: 52)
                            .overlay(Circle().stroke(FAMColor.borderStrong, style: StrokeStyle(lineWidth: 1.2, dash: [4, 3])))
                    }
                    .buttonStyle(.press)
                    .accessibilityLabel("Invite someone")
                }
            }
        }
    }

    private func famFace(_ p: WaitlistFamPerson) -> some View {
        let name = p.name ?? ""
        let handle = p.handle ?? ""
        let first = (name.isEmpty ? handle : name).split(separator: " ").first.map(String.init) ?? ""
        return VStack(spacing: 4) {
            Face(name: name, handle: handle, avatar: p.avatar ?? "", size: 52)
            Text(first)
                .font(FAMFont.body(11.5, .semibold))
                .foregroundStyle(FAMColor.text)
                .lineLimit(1)
            if p.waitlisted != true {
                Text("in FAM").monoLabel(8)
            }
        }
        .frame(width: 58)
    }

    /// Typing waits a moment (250 ms), then asks from two characters.
    private func search() async {
        let term = words.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard term.count >= 2 else {
            found = []
            searched = ""
            return
        }
        try? await Task.sleep(nanoseconds: 250_000_000)
        if Task.isCancelled { return }
        let people = (try? await model.api.waitlistPeople(term)) ?? []
        if Task.isCancelled { return }
        found = people
        searched = term
    }

    private func add(_ p: WaitlistPerson) {
        adding.insert(p.userId)
        Task {
            do {
                let answer = try await model.api.waitlistAdd(userID: p.userId)
                let friend = answer.friend == true
                let named = p.name.isEmpty ? "@" + p.handle : p.name
                found = found.map { row in
                    guard row.userId == p.userId else { return row }
                    return WaitlistPerson(userId: row.userId, name: row.name, handle: row.handle,
                                          avatar: row.avatar, initials: row.initials,
                                          waitlisted: row.waitlisted, added: true, friend: friend)
                }
                model.nowPlaying.say(friend ? "You and " + named + " are FAM"
                                            : "Added. They\u{2019}re FAM when they add you back.")
                if friend { await reload() }
            } catch {
                model.nowPlaying.say(SocialModel.sentence(error, else: "Something went wrong. Try again."))
            }
            adding.remove(p.userId)
        }
    }
}
