import FAMSafety
import SwiftUI

/// Settings (`#screen-settings`, `renderSettings`, shots 20-23): the web's
/// rows in the web's order and words, each an editor, never the first run
/// again (`op-profile-hub`). Toggle rows stay rows whose value reads On /
/// Off, as the web draws them. Where iOS has no such control the row is
/// left out rather than drawn dead (`no-dead-controls`): Dark mode (light
/// only, §250) and "Hey FAM" (no voice search here). iOS adds what the web
/// has no need of: the sign-in methods (Apple, Google), Friends and Messages.
struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var you: YouModel
    @EnvironmentObject private var social: SocialModel
    @EnvironmentObject private var player: StreamPlayer
    @StateObject private var flow = AvatarFlow()

    @State private var editing = false
    @State private var locating = false
    @State private var interestsOpen = false
    @State private var activation: Activation?
    @State private var sheet: SettingsSheet?
    @State private var blocked: [BlockedPerson] = []
    @State private var plans: PlansPage?
    @State private var textAsk: TextAsk?
    @State private var page: WebPage?
    @AppStorage(FAMAPI.zoneKey) private var pinnedZone = ""

    var body: some View {
        VStack(spacing: 0) {
            YouBackRow(title: "Settings") { back() }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    youGroup
                    listeningGroup
                    PrivacyRows(consent: model.consent, open: open)
                    accountGroup
                    friendsGroup
                }
                .padding(.horizontal, 18)
                .padding(.top, 2)
                .padding(.bottom, 18)
            }
        }
        .background(FAMColor.background)
        .modifier(SettingsPresentations(flow: flow, editing: $editing, locating: $locating,
                                        interestsOpen: $interestsOpen, activation: $activation,
                                        sheet: $sheet, textAsk: $textAsk, page: $page,
                                        sheetView: sheetView, savePicture: savePicture))
        .task { await you.loadSettings() }
    }

    private func back() {
        if !model.youPath.isEmpty { model.youPath.removeLast() }
    }

    // MARK: - You

    private var youGroup: some View {
        VStack(alignment: .leading, spacing: 0) {
            SetGroup(text: "You", first: true)
            SetRow(label: "Name and handle", value: you.name.isEmpty ? "Not set" : you.name) { editing = true }
            SetRow(label: "Profile picture", value: you.avatar.isEmpty ? "None" : "Set") {
                flow.start(hasPicture: !you.avatar.isEmpty)
            }
            SetRow(label: "Where you are", value: you.placeLabel) { locating = true }
            SetRow(label: "Date of birth", value: birthValue) { editActivation() }
            SetRow(label: "Gender", value: genderValue) { editActivation() }
            SetRow(label: "Time zone", value: zoneSummary) { sheet = .zone }
        }
    }

    private var birthValue: String {
        guard let a = you.activation else { return "" }
        let b = a.birthDate ?? ""
        return b.isEmpty ? "Not set" : b
    }

    private var genderValue: String {
        guard let a = you.activation else { return "" }
        let g = a.gender ?? ""
        return (a.genders ?? []).first { $0.id == g }?.label ?? "Not set"
    }

    // MARK: - Listening

    private var listeningGroup: some View {
        VStack(alignment: .leading, spacing: 0) {
            SetGroup(text: "Listening")
            SetRow(label: "Your interests", value: you.interestsSummary) { interestsOpen = true }
            SetRow(label: "Listening history", value: "Last 2 weeks") { model.youPath.append(.history) }
            SetRow(label: "My Vibe") { model.youPath.append(.myVibe) }
            SetRow(label: "Save for Later") { model.youPath.append(.saved) }
            SetRow(label: "Default speed", value: FAMAPI.minutes(player.rate) + "x") { sheet = .speed }
            SetRow(label: "Keep the voice's pitch", value: player.keepsPitch ? "On" : "Off") { togglePitch() }
            if let voices = you.voices, !voices.voices.isEmpty {
                SetRow(label: "Voice for your searches", value: voiceLabel(voices)) { sheet = .voice }
            }
        }
    }

    private func voiceLabel(_ page: VoicesPage) -> String {
        page.voices.first { $0.id == page.selected }?.label ?? ""
    }

    private func togglePitch() {
        let on = !player.keepsPitch
        player.setKeepsPitch(on)
        model.nowPlaying.say(on ? "Speed changes keep the voice's pitch" : "Speed changes will shift the voice's pitch")
    }

    // MARK: - Account settings

    private var accountGroup: some View {
        VStack(alignment: .leading, spacing: 0) {
            SetGroup(text: "Account settings")
            SetRow(label: "Subscription", value: you.planLabel ?? "\u{2014}") { openPlans() }
            SetRow(label: "Private search", value: (you.prefs?.privateSearch ?? false) ? "On" : "Off") {
                setPrivateSearch(!(you.prefs?.privateSearch ?? false))
            }
            SetNote(text: "While it\u{2019}s on, every search and Go Deeper you start stays out of the episode library, off your friends\u{2019} DailyFAM and your listening history, and doesn\u{2019}t teach your feed.")
            SetRow(label: "Show my name on episodes I searched",
                   value: (you.prefs?.searchesPublic ?? false) ? "On" : "Off") {
                setSearchesPublic(!(you.prefs?.searchesPublic ?? false))
            }
            SetNote(text: "When it\u{2019}s on, anyone who opens an episode you searched can see your @handle in the player\u{2019}s menu.")
            SetRow(label: "Signed in as", value: model.listener?.email ?? "")
            SetRow(label: "Sign-in methods", value: methodsValue) { model.youPath.append(.signInMethods) }
            SetRow(label: "Change password") { PasswordChange.start(model: model, ask: $textAsk) }
            SetRow(label: "Log out") { Task { await model.signOut() } }
            SetRow(label: "Delete account", danger: true) { sheet = .delete }
            // The web's note says reset is not available (Q1); it is, so
            // that half is said only when this server cannot send the code.
            SetNote(text: you.resetAvailable
                    ? "Deleting your account is immediate and can\u{2019}t be undone."
                    : "Password reset isn\u{2019}t available yet, so keep your password somewhere safe. Deleting your account is immediate and can\u{2019}t be undone.")
        }
    }

    private var methodsValue: String {
        guard let a = you.account else { return "" }
        var names: [String] = []
        if a.hasPassword { names.append("Email") }
        if a.identities.contains(where: { $0.provider == "apple" }) { names.append("Apple") }
        if a.identities.contains(where: { $0.provider == "google" }) { names.append("Google") }
        return names.joined(separator: ", ")
    }

    // MARK: - Friends and messages (iOS: the badge the web never draws, Q10)

    private var friendsGroup: some View {
        VStack(alignment: .leading, spacing: 0) {
            SetGroup(text: "Friends and messages")
            SetRow(label: "Friends", value: friendsLine, badge: social.graph?.newFollowers?.count ?? 0) {
                social.open(.friends)
            }
            SetRow(label: "Messages", value: social.unread > 0 ? "\(social.unread) unread" : "") {
                social.messagesPath = []
                model.tabRequest = .messages
                Task { await social.loadInbox() }
            }
        }
    }

    private var friendsLine: String {
        guard let counts = social.graph?.counts else { return "" }
        let friends = counts.friends ?? 0
        let following = counts.following ?? 0
        return "\(friends) \(friends == 1 ? "friend" : "friends") \u{00B7} \(following) following"
    }

    // MARK: - Time zone (`listener-clock`; on this device only)

    static let zones: [(String, String)] = [
        ("America/New_York", "Eastern"), ("America/Chicago", "Central"),
        ("America/Denver", "Mountain"), ("America/Phoenix", "Arizona"),
        ("America/Los_Angeles", "Pacific"), ("America/Anchorage", "Alaska"),
        ("Pacific/Honolulu", "Hawaii"), ("Europe/London", "London"),
        ("Europe/Paris", "Central Europe"), ("Asia/Kolkata", "India"),
        ("Asia/Tokyo", "Tokyo"), ("Australia/Sydney", "Sydney"),
    ]

    static func zoneLabel(_ id: String) -> String {
        if let known = zones.first(where: { $0.0 == id }) { return known.1 }
        let last = id.split(separator: "/").last.map(String.init) ?? id
        return last.replacingOccurrences(of: "_", with: " ")
    }

    private var zoneSummary: String {
        if !pinnedZone.isEmpty { return Self.zoneLabel(pinnedZone) }
        return "Automatic (" + Self.zoneLabel(TimeZone.current.identifier) + ")"
    }

    private func setZone(_ id: String) {
        pinnedZone = id
        model.nowPlaying.say(id.isEmpty ? "Episodes use this device\u{2019}s time"
                                        : "Episodes use " + Self.zoneLabel(id) + " time")
    }

    // MARK: - The sheets

    @ViewBuilder private func sheetView(_ kind: SettingsSheet) -> some View {
        switch kind {
        case .zone: YouSheet(items: zoneItems).environmentObject(model)
        case .speed: YouSheet(items: speedItems).environmentObject(model)
        case .voice: YouSheet(items: voiceItems).environmentObject(model)
        case .aiOff: YouSheet(items: aiOffItems).environmentObject(model)
        case .blocked: YouSheet(items: blockedItems).environmentObject(model)
        case .delete: YouSheet(items: deleteItems).environmentObject(model)
        case .plans: PlansSheet(page: plans)
        }
    }

    private var zoneItems: [YouSheetItem] {
        let tick = "  \u{2713}"
        var items = [YouSheetItem(label: "Time zone for your episodes", group: true)]
        let auto = "Automatic (" + Self.zoneLabel(TimeZone.current.identifier) + ")" + (pinnedZone.isEmpty ? tick : "")
        items.append(YouSheetItem(label: auto) { setZone("") })
        for z in Self.zones {
            let id = z.0
            items.append(YouSheetItem(label: z.1 + (pinnedZone == id ? tick : "")) { setZone(id) })
        }
        return items
    }

    /// The default speed and, under "Voice", the pitch (`openSpeedMenu`).
    private var speedItems: [YouSheetItem] {
        let tick = "  \u{2713}"
        var items: [YouSheetItem] = []
        let p = player
        for s in [0.5, 0.8, 1.0, 1.2, 1.5, 2.0] {
            items.append(YouSheetItem(label: FAMAPI.minutes(s) + "x" + (s == player.rate ? tick : "")) {
                p.setRate(s)
            })
        }
        items.append(YouSheetItem(label: "Voice", group: true))
        items.append(YouSheetItem(label: "Keep the voice's pitch" + (player.keepsPitch ? tick : "")) {
            togglePitch()
        })
        return items
    }

    /// The voice bank (`openVoiceMenu('settings')`, §147): kept on the
    /// server, so the next search is spoken in it.
    private var voiceItems: [YouSheetItem] {
        let tick = "  \u{2713}"
        var items = [YouSheetItem(label: "Voice for your searches", group: true)]
        let selected = you.voices?.selected ?? ""
        for v in you.voices?.voices ?? [] {
            let id = v.id
            items.append(YouSheetItem(label: v.label + (id == selected ? tick : "")) { chooseVoice(id) })
        }
        return items
    }

    private func chooseVoice(_ id: String) {
        guard id != (you.voices?.selected ?? "") else { return }
        Task {
            try? await model.api.chooseVoice(id)
            await you.loadSettings()
        }
    }

    private var aiOffItems: [YouSheetItem] {
        [YouSheetItem(label: "Stop sending your questions to AI? Searches, Go Deeper and attachments will ask again first.",
                      group: true),
         YouSheetItem(label: "Turn off", danger: true) {
             Task { await model.consent.answer(false) }
         }]
    }

    private var blockedItems: [YouSheetItem] {
        var items = [YouSheetItem(label: "Blocked people \u{00B7} tap to unblock", group: true)]
        for person in blocked {
            let handle = person.handle
            let label = person.name.isEmpty ? "@" + handle : person.name
            let sub = person.name.isEmpty ? "" : "@" + handle
            items.append(YouSheetItem(label: label, sub: sub) { unblock(handle) })
        }
        return items
    }

    private var deleteItems: [YouSheetItem] {
        [YouSheetItem(label: "This deletes everything FAM holds about you, now. It cannot be undone.", group: true),
         YouSheetItem(label: "Delete my account", danger: true) { deleteAccount() }]
    }

    // MARK: - Actions

    private func open(_ action: PrivacyAction) {
        switch action {
        case .aiOff: sheet = .aiOff
        case .aiAsk:
            Task {
                if await model.consent.ensure() { model.nowPlaying.say("Allowed") }
            }
        case .counting(let on): setCounting(on)
        case .blocked: openBlocked()
        case .page(let path): page = WebPage(url: model.api.base.appendingPathComponent(path))
        case .activationValue: break
        }
    }

    /// `editActivation`: read afresh, then the editor.
    private func editActivation() {
        Task {
            if let found = await you.loadActivation() {
                activation = found
            } else {
                model.nowPlaying.say("Couldn\u{2019}t reach FAM. Try again.")
            }
        }
    }

    private func setCounting(_ on: Bool) {
        Task {
            do {
                try await model.api.setCounted(on, version: you.activation?.counting?.version)
                _ = await you.loadActivation()
            } catch {
                model.nowPlaying.say("Couldn\u{2019}t save that. Try again.")
            }
        }
    }

    private func openBlocked() {
        Task {
            guard let people = try? await model.safety.blocked() else {
                model.nowPlaying.say("Couldn\u{2019}t reach FAM. Try again.")
                return
            }
            if people.isEmpty {
                model.nowPlaying.say("You haven't blocked anyone")
                return
            }
            blocked = people
            sheet = .blocked
        }
    }

    private func unblock(_ handle: String) {
        Task {
            do {
                try await model.safety.unblock(handle: handle)
                model.nowPlaying.say("Unblocked @" + handle)
            } catch {
                model.nowPlaying.say("Couldn\u{2019}t unblock them")
            }
            await you.refreshBlocked()
        }
    }

    private func openPlans() {
        Task {
            plans = try? await model.api.plans()
            sheet = .plans
        }
    }

    private func setPrivateSearch(_ on: Bool) {
        Task {
            if !(await you.savePreference(["private_search": on])) {
                model.nowPlaying.say("Could not save that")
            }
        }
    }

    private func setSearchesPublic(_ on: Bool) {
        Task {
            if await you.savePreference(["searches_public": on]) {
                model.nowPlaying.say(on ? "Your @handle shows on episodes you searched"
                                        : "Episodes you searched no longer name you")
            } else {
                model.nowPlaying.say("Could not save that")
            }
        }
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

    /// The picture from Settings: with no name or handle yet, the web says
    /// so and opens Edit profile.
    private func savePicture(_ url: String) {
        let name = you.name
        let handle = you.handle
        guard !name.isEmpty, handle.count >= 3 else {
            model.nowPlaying.say("Set your name and handle first")
            editing = true
            return
        }
        Task {
            if let refusal = await you.saveAvatar(url, name: name, handle: handle) {
                model.nowPlaying.say(refusal)
            } else {
                model.nowPlaying.say("Picture saved")
            }
        }
    }
}

/// Which bottom sheet Settings has up.
enum SettingsSheet: String, Identifiable {
    case zone, speed, voice, aiOff, blocked, delete, plans
    var id: String { rawValue }
}

/// What a privacy row asks Settings to do.
enum PrivacyAction {
    case aiOff, aiAsk, counting(Bool), blocked, page(String), activationValue
}

/// Everything Settings presents, kept out of its body.
private struct SettingsPresentations<Sheet: View>: ViewModifier {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var flow: AvatarFlow
    @Binding var editing: Bool
    @Binding var locating: Bool
    @Binding var interestsOpen: Bool
    @Binding var activation: Activation?
    @Binding var sheet: SettingsSheet?
    @Binding var textAsk: TextAsk?
    @Binding var page: WebPage?
    let sheetView: (SettingsSheet) -> Sheet
    let savePicture: (String) -> Void

    func body(content: Content) -> some View {
        content
            .avatarFlow(flow, model: model, save: savePicture)
            .textModal($textAsk)
            .sheet(item: $sheet) { kind in sheetView(kind) }
            .sheet(item: $page) { p in SafariView(url: p.url).ignoresSafeArea() }
            .fullScreenCover(isPresented: $editing) { IdentityView(mode: .edit).famObjects(model) }
            .fullScreenCover(isPresented: $locating) { LocationView().famObjects(model) }
            .fullScreenCover(isPresented: $interestsOpen) { InterestsView(mode: .settings).famObjects(model) }
            .fullScreenCover(item: $activation) { a in
                ActivationCard(activation: a).famObjects(model)
            }
    }
}

extension Activation: Identifiable {
    var id: String { (birthDate ?? "") + "|" + (gender ?? "") }
}

/// Privacy and safety (App Store 1.2, 5.1.2(i)): the AI answer, being
/// counted, blocked people, and the three pages. Observes the consent so
/// its row follows a yes given anywhere.
struct PrivacyRows: View {
    @ObservedObject var consent: ConsentModel
    let open: (PrivacyAction) -> Void
    @EnvironmentObject private var you: YouModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SetGroup(text: "Privacy and safety")
            SetRow(label: "Send my questions to AI", value: aiValue) {
                open(consent.current?.given == true ? .aiOff : .aiAsk)
            }
            SetNote(text: "FAM writes episodes for what you ask with Anthropic\u{2019}s Claude, and searches for sources with Parallel and Perplexity. Turn this off and you can still listen to everything FAM writes ahead.")
            SetRow(label: "Count me in FAM\u{2019}s totals", value: counted ? "On" : "Off") {
                open(.counting(!counted))
            }
            SetNote(text: "FAM counts what groups of listeners hear, by age group, gender and region, only in groups of 100 or more. You\u{2019}re never picked out, and none of it is shown or sent to anyone.")
            SetRow(label: "Blocked people", value: blockedValue) { open(.blocked) }
            SetRow(label: "Terms and conditions") { open(.page("terms")) }
            SetRow(label: "Privacy policy") { open(.page("privacy")) }
            SetRow(label: "Help and support") { open(.page("support")) }
        }
    }

    private var counted: Bool { you.activation?.counting?.counted ?? false }

    /// "Allowed", "Off", "Not asked yet", or nothing while it loads.
    private var aiValue: String {
        guard let c = consent.current else { return "" }
        if c.given { return "Allowed" }
        return c.asked ? "Off" : "Not asked yet"
    }

    private var blockedValue: String {
        guard let n = you.blockedCount else { return "" }
        return n == 0 ? "None" : String(n)
    }
}

// MARK: - Your details: date of birth, place, gender (`#activationOverlay`)

/// "Your details" (shot 35): the date (required, under 13 asked to be
/// confirmed, §411), one box for the place, the gender chips (the chosen
/// one tapped again clears it), the counting notice with its Privacy
/// policy link, Continue once a date is set. Every help line is the server's.
struct ActivationCard: View {
    let activation: Activation
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var you: YouModel
    @Environment(\.dismiss) private var dismiss
    @State private var date = Date()
    @State private var dateSet = false
    @State private var place = ""
    @State private var placeShown = ""
    @State private var gender = ""
    @State private var confirm: String?
    @State private var gone: String?
    @State private var working = false

    var body: some View {
        ModalCard(close: { if gone == nil { dismiss() } }) {
            if let gone {
                Text(gone)
                    .font(FAMFont.body(13))
                    .foregroundStyle(FAMColor.text)
                    .fixedSize(horizontal: false, vertical: true)
                ModalButton(label: "OK", primary: true, wide: true) { finishGone() }
                    .padding(.top, 16)
            } else if let confirm {
                confirmView(confirm)
            } else {
                form
            }
        }
        .onAppear { fill() }
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top) {
                Text("Your details")
                    .font(FAMFont.heading(16))
                    .foregroundStyle(FAMColor.text)
                Spacer()
                CloseCircle(label: "Close") { dismiss() }
            }
            .padding(.bottom, 10)
            FieldLabel(text: "Date of birth")
            DatePicker("", selection: dateBinding, in: ...Date(), displayedComponents: .date)
                .labelsHidden()
                .datePickerStyle(.compact)
                .tint(FAMColor.copper)
                .opacity(dateSet ? 1 : 0.6)
            help(activation.help?.birthDate)
            FieldLabel(text: "Where you live").padding(.top, 12)
            TextField("", text: $place, prompt: Text("Town, state").foregroundColor(FAMColor.textFaint))
                .youInput()
            help(activation.help?.location)
            FieldLabel(text: "Gender").padding(.top, 12)
            FlowRow(spacing: 8) {
                ForEach(activation.genders ?? [], id: \.id) { g in genderChip(g) }
            }
            help(activation.help?.gender)
            notice
            ModalButton(label: "Continue", primary: true, wide: true, enabled: dateSet && !working) {
                submit(confirmed: false)
            }
            .padding(.top, 14)
        }
    }

    private var dateBinding: Binding<Date> {
        Binding(get: { date }, set: { date = $0; dateSet = true })
    }

    @ViewBuilder private func help(_ text: String?) -> some View {
        if let text, !text.isEmpty {
            Text(text)
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.textMuted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 6)
        }
    }

    private func genderChip(_ g: ActivationGender) -> some View {
        let on = gender == g.id
        return Button { gender = on ? "" : g.id } label: {
            Text(g.label)
                .font(FAMFont.body(12))
                .foregroundStyle(on ? FAMColor.ink : FAMColor.text)
                .padding(.horizontal, 12)
                .frame(minHeight: 32)
                .background(on ? FAMColor.copper : FAMColor.background, in: Capsule())
                .overlay(Capsule().stroke(on ? FAMColor.copper : FAMColor.borderStrong))
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    /// The counting notice, worded by the server, with its Privacy policy link.
    @ViewBuilder private var notice: some View {
        let n = activation.counting?.notice
        let words = n?.body ?? ""
        if !words.isEmpty {
            let path = n?.privacyUrl ?? ""
            let address = model.api.base.absoluteString + path
            let link = path.isEmpty ? "" : " [Privacy policy](" + address + ")"
            let text = (try? AttributedString(markdown: words + link)) ?? AttributedString(words)
            Text(text)
                .font(FAMFont.body(11.5))
                .foregroundStyle(FAMColor.textMuted)
                .tint(FAMColor.copper)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 12)
        }
    }

    private func confirmView(_ question: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(question)
                .font(FAMFont.heading(16))
                .foregroundStyle(FAMColor.text)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Spacer()
                ModalButton(label: "Change it") { confirm = nil }
                ModalButton(label: "Yes", primary: true, enabled: !working) { submit(confirmed: true) }
            }
            .padding(.top, 16)
        }
    }

    private func fill() {
        if let b = activation.birthDate, !b.isEmpty, let d = Self.day.date(from: String(b.prefix(10))) {
            date = d
            dateSet = true
        }
        let label = activation.location?.label ?? ""
        place = label
        placeShown = label
        gender = activation.gender ?? ""
    }

    static let day: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    private func submit(confirmed: Bool) {
        guard dateSet else { return }
        var body: [String: Any] = ["birth_date": Self.localDay(date), "gender": gender]
        let typed = place.trimmingCharacters(in: .whitespacesAndNewlines)
        if typed != placeShown {
            body["city"] = typed
            body["region"] = NSNull()
            body["country"] = NSNull()
        }
        if confirmed { body["confirm_under_13"] = true }
        working = true
        Task {
            do {
                let answer = try await model.api.saveActivation(body)
                working = false
                if let question = answer.confirm, !question.isEmpty {
                    confirm = question
                    return
                }
                if answer.deleted == true {
                    gone = answer.message ?? "We've deleted your account."
                    return
                }
                model.nowPlaying.say("Saved")
                _ = await you.loadActivation()
                await you.loadPrefs()
                dismiss()
            } catch {
                working = false
                model.nowPlaying.say(SocialModel.sentence(error, else: "Couldn\u{2019}t save that. Try again."))
            }
        }
    }

    /// The day the picker shows, in the phone's own calendar.
    static func localDay(_ d: Date) -> String {
        let c = Calendar.current.dateComponents([.year, .month, .day], from: d)
        return String(format: "%04d-%02d-%02d", c.year ?? 2000, c.month ?? 1, c.day ?? 1)
    }

    private func finishGone() {
        dismiss()
        let app = model
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 350_000_000)
            app.accountErased()
        }
    }
}

// MARK: - Subscription (`openPlans`, shot 36)

/// "YOUR PLAN": every tier the server lists - a tick on yours, its name with
/// "YOUR PLAN", the blurb and the limits in the refusal's words - then the
/// note, worded from the server's `enforced` (§207), and Close. No checkout
/// (`tiers-off`): another tier says so when tapped rather than doing nothing.
struct PlansSheet: View {
    let page: PlansPage?
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Your plan").monoLabel(9.5, color: FAMColor.textFaint, tracking: 1.14)
                        .padding(.bottom, 14)
                    if let page {
                        ForEach(page.tiers, id: \.name) { tier in row(tier, current: page.current ?? "free") }
                        if page.checkout != true {
                            EmptyNote(text: page.enforced == true
                                      ? "Upgrades aren\u{2019}t available yet. Your daily allowance comes back every day."
                                      : "Upgrades aren\u{2019}t available yet. Everything is free for now.")
                                .padding(.top, 6)
                        }
                    } else {
                        Text("Plans could not load. Check your connection and try again.")
                            .font(FAMFont.body(12))
                            .foregroundStyle(FAMColor.textMuted)
                    }
                }
                .padding(.horizontal, 22)
                .padding(.top, 20)
            }
            SheetCancelLabel(label: "Close") { dismiss() }
        }
        .padding(.bottom, 12)
        .background(FAMColor.surface)
        .presentationDetents([.large])
        .presentationBackground(FAMColor.surface)
        .presentationCornerRadius(20)
    }

    private func row(_ tier: PlanTier, current: String) -> some View {
        let mine = tier.name == current
        let name = tier.label ?? tier.name
        return Button {
            if !mine { model.nowPlaying.say("No checkout yet \u{2014} " + tier.name + " cannot be bought from here") }
        } label: {
            HStack(alignment: .top, spacing: 14) {
                ZStack {
                    Circle().fill(mine ? FAMColor.copper : Color.clear)
                    Circle().stroke(mine ? FAMColor.copper : FAMColor.borderStrong, lineWidth: 1.5)
                    if mine {
                        WebIcon(path: YouIcons.check, size: 11, line: 3).foregroundStyle(FAMColor.ink)
                    }
                }
                .frame(width: 20, height: 20)
                .padding(.top, 2)
                VStack(alignment: .leading, spacing: 5) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(name)
                            .font(FAMFont.heading(15, .semibold))
                            .foregroundStyle(FAMColor.text)
                        if mine { Text("Your plan").monoLabel(8.5, color: FAMColor.copper, bold: true) }
                    }
                    if let blurb = tier.blurb, !blurb.isEmpty {
                        Text(blurb)
                            .font(FAMFont.body(12))
                            .foregroundStyle(FAMColor.textMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Text(Self.limits(tier)).monoLabel(8.5, color: FAMColor.textFaint, tracking: 0.85)
                        .padding(.top, 2)
                }
                Spacer(minLength: 0)
            }
            .padding(.vertical, 12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// `planLimits`: "5 episodes a day · Unlimited Explore episodes".
    static func limits(_ tier: PlanTier) -> String {
        let limits = tier.limits ?? []
        if limits.isEmpty { return "No ceiling" }
        let parts: [String] = limits.map { l in
            let per = l.window == "week" ? "a week" : "a day"
            let what = l.resource == "explore" ? "Explore episodes" : "episodes"
            let count = l.count ?? 0
            if l.unlimited == true || count < 0 { return "Unlimited " + what }
            return "\(count) " + what + " " + per
        }
        return parts.joined(separator: "  \u{00B7}  ")
    }
}

/// The sheet's white bar with another word than Cancel ("Close").
struct SheetCancelLabel: View {
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(FAMFont.body(13, .bold))
                .foregroundStyle(FAMColor.textMuted)
                .frame(maxWidth: .infinity)
                .padding(12)
                .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }
}

// MARK: - Sign-in methods (iOS only)

/// How this account signs in, and linking Apple or Google to it, so a
/// later "Sign in with Apple" lands here rather than making a new account
/// (the server never joins accounts by email). Unlink while another way in
/// remains (`unlink_identity`).
struct SignInMethodsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var info: AccountInfo?
    @State private var linkProblem = ""
    @State private var linking = false

    var body: some View {
        VStack(spacing: 0) {
            YouBackRow(title: "Sign-in methods") {
                if !model.youPath.isEmpty { model.youPath.removeLast() }
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    SetGroup(text: "How you sign in", first: true)
                    if info?.hasPassword == true {
                        methodRow("Email and password", linked: true, provider: nil)
                    }
                    methodRow("Apple", linked: isLinked("apple"), provider: "apple")
                    if ProviderButtons.googleConfigured {
                        methodRow("Google", linked: isLinked("google"), provider: "google")
                    }
                    if !linkProblem.isEmpty {
                        Text(linkProblem)
                            .font(FAMFont.body(12))
                            .foregroundStyle(FAMColor.coral)
                            .padding(.bottom, 8)
                    }
                    SetNote(text: "Link Apple or Google here and they sign you in to this account.")
                        .padding(.top, 6)
                }
                .padding(.horizontal, 18)
                .padding(.top, 2)
                .padding(.bottom, 18)
            }
        }
        .background(FAMColor.background)
        .task(id: model.listener?.userId) { info = try? await model.api.account() }
    }

    private func isLinked(_ provider: String) -> Bool {
        info?.identities.contains(where: { $0.provider == provider }) ?? false
    }

    @ViewBuilder
    private func methodRow(_ name: String, linked: Bool, provider: String?) -> some View {
        if linked {
            HStack(spacing: 10) {
                Text(name)
                    .font(FAMFont.body(13, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "checkmark.circle.fill").foregroundStyle(FAMColor.sage)
                    .accessibilityLabel("Linked")
                if let provider, canUnlink(provider) {
                    Button("Unlink") { unlink(provider) }
                        .font(FAMFont.body(12, .semibold))
                        .foregroundStyle(FAMColor.coral)
                        .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 13)
            .frame(minHeight: 45)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
            .padding(.bottom, 8)
        } else if let provider {
            VStack(alignment: .leading, spacing: 8) {
                Text("Link \(name)").font(FAMFont.body(13, .semibold)).foregroundStyle(FAMColor.text)
                ProviderButtons(making: false, agreed: true, problem: $linkProblem, working: $linking,
                                linking: true, showApple: provider == "apple", showGoogle: provider == "google")
                    .onChange(of: linking) { _, busy in
                        if !busy { Task { info = try? await model.api.account() } }
                    }
            }
            .padding(14)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
            .padding(.bottom, 8)
        }
    }

    /// The server's rule (`unlink_identity`): another way in must remain.
    private func canUnlink(_ provider: String) -> Bool {
        if info?.hasPassword == true { return true }
        return info?.identities.contains(where: { $0.provider != provider }) ?? false
    }

    private func unlink(_ provider: String) {
        Task {
            do {
                try await model.api.unlink(provider: provider)
                info = try? await model.api.account()
            } catch {
                linkProblem = error.localizedDescription
            }
        }
    }
}
