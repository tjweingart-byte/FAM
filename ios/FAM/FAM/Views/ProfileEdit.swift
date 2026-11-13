import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

// MARK: - Your picture (`pickAvatar`, `saveAvatar`, the photo editor)

/// A photo chosen and waiting in the editor.
struct EditingPhoto: Identifiable {
    let id = UUID()
    let image: UIImage
}

/// Changing your picture, from Edit profile or Settings: with none, where
/// the photo comes from (library, camera, file - the choices iOS's own file
/// input offers the web); with one, Move and scale (when the original was
/// chosen on this phone), Choose a different photo, Remove photo. Every
/// photo goes through the editor and leaves as 256 px JPEG.
@MainActor
final class AvatarFlow: ObservableObject {
    @Published var menu = false
    @Published var sources = false
    @Published var library = false
    @Published var camera = false
    @Published var files = false
    @Published var item: PhotosPickerItem?
    @Published var editing: EditingPhoto?

    func start(hasPicture: Bool) {
        if hasPicture { menu = true } else { sources = true }
    }

    /// A second presentation waits for the first to finish closing.
    func later(_ run: @escaping () -> Void) {
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 350_000_000)
            run()
        }
    }
}

extension View {
    /// The sheets, pickers and editor of `AvatarFlow`; `save` gets the new
    /// picture as a data URL, or "" to take it off.
    func avatarFlow(_ flow: AvatarFlow, model: AppModel, save: @escaping (String) -> Void) -> some View {
        modifier(AvatarFlowModifier(flow: flow, model: model, save: save))
    }
}

struct AvatarFlowModifier: ViewModifier {
    @ObservedObject var flow: AvatarFlow
    let model: AppModel
    let save: (String) -> Void

    func body(content: Content) -> some View {
        content
            .famSheet(isPresented: $flow.menu) { menuSheet }
            .famSheet(isPresented: $flow.sources) { sourcesSheet }
            .photosPicker(isPresented: $flow.library, selection: $flow.item, matching: .images)
            .fileImporter(isPresented: $flow.files, allowedContentTypes: [.image]) { result in
                picked(file: result)
            }
            .fullScreenCover(isPresented: $flow.camera) {
                CameraPicker { data in
                    flow.camera = false
                    if let data {
                        flow.later { open(data) }
                    }
                }
                .ignoresSafeArea()
            }
            .famSheet(item: $flow.editing) { e in
                PhotoEditor(image: e.image, save: save)
            }
            .onChange(of: flow.item) { _, item in
                guard let item else { return }
                flow.item = nil
                Task {
                    guard let data = try? await item.loadTransferable(type: Data.self) else {
                        model.nowPlaying.say("Could not read that file")
                        return
                    }
                    flow.later { open(data) }
                }
            }
    }

    private var menuSheet: some View {
        var items: [YouSheetItem] = []
        let source = AvatarSource.load()
        if source == nil {
            items.append(YouSheetItem(label: "This photo was set on another device, so only a new one can be cropped here.",
                                      group: true))
        } else if let source {
            items.append(YouSheetItem(label: "Move and scale") { flow.editing = EditingPhoto(image: source) })
        }
        items.append(YouSheetItem(label: "Choose a different photo") { flow.sources = true })
        items.append(YouSheetItem(label: "Remove photo", danger: true) {
            AvatarSource.clear()
            save("")
        })
        return YouSheet(items: items).environmentObject(model)
    }

    private var sourcesSheet: some View {
        var items: [YouSheetItem] = [YouSheetItem(label: "Choose from library") { flow.library = true }]
        if UIImagePickerController.isSourceTypeAvailable(.camera) {
            items.append(YouSheetItem(label: "Take a photo") { flow.camera = true })
        }
        items.append(YouSheetItem(label: "Choose a file") { flow.files = true })
        return YouSheet(items: items).environmentObject(model)
    }

    private func picked(file result: Result<URL, Error>) {
        guard case let .success(url) = result else { return }
        let reading = url.startAccessingSecurityScopedResource()
        defer { if reading { url.stopAccessingSecurityScopedResource() } }
        guard let data = try? Data(contentsOf: url) else {
            model.nowPlaying.say("Could not read that file")
            return
        }
        flow.later { open(data) }
    }

    /// Downscaled to 1024 px, kept for Move and scale, then the editor.
    private func open(_ data: Data) {
        guard let image = AvatarSource.prepare(data) else {
            model.nowPlaying.say("That file is not an image FAM can read")
            return
        }
        AvatarSource.keep(image)
        flow.editing = EditingPhoto(image: image)
    }
}

/// The circle a picture is cut to, and the dim round it.
struct HoleShape: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path(rect)
        p.addEllipse(in: rect)
        return p
    }
}

/// "Move and scale" (`#photoOverlay`, shot 15): a 270 pt stage on ink,
/// the circle with a faint ring and the dim round it; drag to move, the
/// slider zooms 1-3x about the middle, the picture always covers the
/// circle; the AI-check sentence before Save; Save cuts 256 px JPEG 0.85.
struct PhotoEditor: View {
    let image: UIImage
    let save: (String) -> Void
    /// A mix's cover (`cover-mode`, `coverChosen`): the whole square kept,
    /// framed rather than cut to a circle, "Position your cover", 360 px.
    var square: Bool = false
    var title: String = "Move and scale"
    var pixels: CGFloat = 256
    @Environment(\.dismiss) private var dismiss
    @State private var zoom: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var dragFrom: CGSize?

    private let stage: CGFloat = 270
    private static let ink = Color(hex: 0x18151F)

    var body: some View {
        ModalCard(close: { dismiss() }) {
            HStack(alignment: .top) {
                Text(title)
                    .font(FAMFont.heading(16))
                    .foregroundStyle(FAMColor.text)
                Spacer()
                Button { dismiss() } label: {
                    WebIcon(path: Icons.close, size: 14, line: 2.4)
                        .foregroundStyle(FAMColor.textMuted)
                        .frame(width: 28, height: 28)
                }
                .buttonStyle(.press)
                .accessibilityLabel("Close")
            }
            .padding(.bottom, 12)
            stageView
            Slider(value: zoomBinding, in: 1...3)
                .tint(FAMColor.copper)
                .padding(.top, 14)
                .accessibilityLabel("Zoom")
            Text(MixCover.note)
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.textMuted)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 12)
            HStack(spacing: 8) {
                Spacer()
                ModalButton(label: "Cancel") { dismiss() }
                ModalButton(label: "Save", primary: true) { finish() }
            }
            .padding(.top, 12)
        }
    }

    private var stageView: some View {
        let size = shown(zoom)
        return ZStack {
            Self.ink
            Image(uiImage: image)
                .resizable()
                .frame(width: size.width, height: size.height)
                .offset(offset)
            mask
        }
        .frame(width: stage, height: stage)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .contentShape(Rectangle())
        .gesture(drag)
        .frame(maxWidth: .infinity)
    }

    /// The circle cut out of a wash; a cover's square is kept whole, so
    /// its mask is a frame (`#photoOverlay.cover-mode .photo-mask`).
    @ViewBuilder private var mask: some View {
        if square {
            RoundedRectangle(cornerRadius: 14)
                .stroke(Color(hex: 0xF4EFE4).opacity(0.5), lineWidth: 1.5)
        } else {
            HoleShape()
                .fill(Self.ink.opacity(0.62), style: FillStyle(eoFill: true))
            Circle()
                .stroke(Color(hex: 0xF4EFE4).opacity(0.35), lineWidth: 1)
        }
    }

    private var drag: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                let start = dragFrom ?? offset
                if dragFrom == nil { dragFrom = offset }
                let moved = CGSize(width: start.width + value.translation.width,
                                   height: start.height + value.translation.height)
                offset = clamp(moved, zoom: zoom)
            }
            .onEnded { _ in dragFrom = nil }
    }

    /// Zoom about the middle: the point under the circle's centre stays.
    private var zoomBinding: Binding<CGFloat> {
        Binding(get: { zoom }, set: { next in
            let ratio = zoom > 0 ? next / zoom : 1
            let scaled = CGSize(width: offset.width * ratio, height: offset.height * ratio)
            zoom = next
            offset = clamp(scaled, zoom: next)
        })
    }

    /// Points per image pixel at zoom 1: the short side fills the stage.
    private var fit: CGFloat {
        let short = min(image.size.width, image.size.height)
        return short > 0 ? stage / short : 1
    }

    private func shown(_ z: CGFloat) -> CGSize {
        CGSize(width: image.size.width * fit * z, height: image.size.height * fit * z)
    }

    private func clamp(_ o: CGSize, zoom z: CGFloat) -> CGSize {
        let size = shown(z)
        let maxX = max(0, (size.width - stage) / 2)
        let maxY = max(0, (size.height - stage) / 2)
        return CGSize(width: min(max(o.width, -maxX), maxX), height: min(max(o.height, -maxY), maxY))
    }

    private func finish() {
        guard let url = cut() else { return }
        dismiss()
        let save = self.save
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 350_000_000)
            save(url)
        }
    }

    /// The circle's square (or the cover's), 256 px (360 for a cover),
    /// JPEG 0.85, as a data URL (`savePhotoCrop`).
    private func cut() -> String? {
        let perPixel = fit * zoom
        guard perPixel > 0 else { return nil }
        let side = stage / perPixel
        let cx = image.size.width / 2 - offset.width / perPixel
        let cy = image.size.height / 2 - offset.height / perPixel
        let out: CGFloat = pixels
        let k = out / side
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: out, height: out), format: format)
        let source = image
        let picture = renderer.image { _ in
            let rect = CGRect(x: -(cx - side / 2) * k, y: -(cy - side / 2) * k,
                              width: source.size.width * k, height: source.size.height * k)
            source.draw(in: rect)
        }
        guard let jpeg = picture.jpegData(compressionQuality: 0.85) else { return nil }
        return "data:image/jpeg;base64," + jpeg.base64EncodedString()
    }
}

// MARK: - Edit profile and the first run's "Who are you?" (`screen-identity`)

/// One interest chip in Edit profile.
struct InterestOption: Hashable {
    let id: String
    let label: String
}

/// Edit profile and the identity step, one screen (`identityMode`, §5.8).
/// The mode decides three things: the X (edit only), the button ("Save" /
/// "Next") and where saving goes (back to where it was opened from / on to
/// the interests). The first run alone shows "required" and "Skip for
/// now"; editing alone shows your interests and the account. The phone
/// number is off on iOS 1.0 (the owner's decision; APP_STORE.md gap 12).
struct IdentityView: View {
    enum Mode { case edit, firstRun }

    let mode: Mode
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var you: YouModel
    @Environment(\.dismiss) private var dismiss
    @StateObject private var flow = AvatarFlow()

    @State private var name = ""
    @State private var handle = ""
    @State private var city = ""
    @State private var region = ""
    @State private var country = ""
    @State private var handleNote = IdentityView.handleHelp
    @State private var handleBad = false
    @State private var options: [InterestOption] = []
    @State private var picked: [String] = []
    @State private var typed: [String] = []
    @State private var dirty = false
    @State private var adding = ""
    @State private var nameMissing = false
    @State private var asking = false
    @State private var saving = false
    @State private var filled = false
    @State private var textAsk: TextAsk?
    @FocusState private var focus: Field?

    enum Field { case name, handle }

    static let handleHelp = "Letters, numbers and underscores. This is what people search for."

    private var editing: Bool { mode == .edit }
    private var maxInterests: Int { max(1, you.profile?.interestsMax ?? 5) }

    var body: some View {
        screen
            .avatarFlow(flow, model: model) { url in savePicture(url) }
            .textModal($textAsk)
            .overlay {
                if asking { RequiredPopup { closeRequired() } }
            }
            .overlay(alignment: .bottom) { ToastView().padding(.bottom, 96) }
            .task { await fill() }
    }

    private var screen: some View {
        VStack(spacing: 0) {
            if editing {
                HStack {
                    Spacer()
                    CloseCircle(label: "Close") { dismiss() }
                }
                .padding(.horizontal, 16)
                .padding(.top, 14)
            } else {
                StepMarks(step: 2)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    heading
                    pictureRow
                    nameField
                    handleField
                    if editing { interestsBlock }
                    placeBlock
                    if editing { accountBlock }
                }
                .padding(.horizontal, 40)
                .padding(.top, editing ? 18 : 30)
                .padding(.bottom, 24)
            }
            .scrollDismissesKeyboard(.interactively)
            dock
        }
        .background(FAMColor.background.ignoresSafeArea())
    }

    private var heading: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(editing ? "Edit profile" : "Who are you?")
                .font(FAMFont.heading(20, .semibold))
                .foregroundStyle(FAMColor.text)
            Text(editing ? "This is how your friends see you."
                         : "Your name and @handle are how friends find you. You can change them any time.")
                .font(FAMFont.body(13))
                .foregroundStyle(FAMColor.textMuted)
                .lineSpacing(5)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 14)
                .padding(.bottom, 26)
        }
    }

    /// `.id-pic-row`: the 78 pt picture with the copper camera badge, and
    /// "TAP TO CHANGE" / "ADD A PHOTO". A tap is `pickAvatar`.
    private var pictureRow: some View {
        let avatar = you.avatar
        return Button { flow.start(hasPicture: !avatar.isEmpty) } label: {
            HStack(spacing: 14) {
                Face(name: name, handle: handle, avatar: avatar, size: 78, initialsSize: 26)
                    .overlay(alignment: .bottomTrailing) {
                        WebIcon(path: YouIcons.camera, size: 12)
                            .foregroundStyle(FAMColor.ink)
                            .frame(width: 20, height: 20)
                            .background(FAMColor.copper, in: Circle())
                            .padding(2)
                            .background(FAMColor.background, in: Circle())
                    }
                Text(avatar.isEmpty ? "Add a photo" : "Tap to change")
                    .monoLabel(9, color: FAMColor.textFaint, tracking: 0.72)
                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityLabel(avatar.isEmpty ? "Add a photo" : "Change your photo")
        .padding(.top, 4)
        .padding(.bottom, 20)
    }

    private var nameField: some View {
        VStack(alignment: .leading, spacing: 0) {
            FieldLabel(text: "Your name", required: !editing)
            TextField("", text: $name, prompt: Text("Your name").foregroundColor(FAMColor.textFaint))
                .textContentType(.name)
                .focused($focus, equals: .name)
                .submitLabel(.next)
                .onSubmit { focus = .handle }
                .onChange(of: name) { _, value in
                    if !value.trimmingCharacters(in: .whitespaces).isEmpty { nameMissing = false }
                }
                .youInput(missing: nameMissing)
        }
        .padding(.bottom, 13)
    }

    /// The @ inside the field; the handle cleaned as it is typed
    /// (`cleanHandleInput`); the note under it says what is wrong -
    /// "@x is taken." comes from the server at save (Q5).
    private var handleField: some View {
        VStack(alignment: .leading, spacing: 0) {
            FieldLabel(text: "Username")
            HStack(spacing: 4) {
                Text("@")
                    .font(FAMFont.mono(13))
                    .foregroundStyle(FAMColor.textFaint)
                TextField("", text: $handle, prompt: Text("yourhandle").foregroundColor(FAMColor.textFaint))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(.username)
                    .focused($focus, equals: .handle)
                    .submitLabel(.done)
                    .onChange(of: handle) { _, value in
                        let clean = Self.cleanHandle(value)
                        if clean != value { handle = clean }
                    }
            }
            .youInput()
            Text(handleNote)
                .font(FAMFont.body(10.5))
                .foregroundStyle(handleBad ? FAMColor.coral : FAMColor.textFaint)
                .lineSpacing(5)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 7)
        }
        .padding(.bottom, 13)
    }

    static func cleanHandle(_ text: String) -> String {
        var s = Substring(text)
        while s.hasPrefix("@") { s = s.dropFirst() }
        let allowed = s.filter { ch in
            ch == "_" || (ch.isASCII && (ch.isLetter || ch.isNumber))
        }
        return String(allowed).lowercased()
    }

    // MARK: Your interests (edit only)

    private var interestsBlock: some View {
        let full = picked.count >= maxInterests
        return VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Your interests").monoLabel(10, color: FAMColor.copper, tracking: 1.6)
                Spacer()
                Text("\(picked.count) of \(maxInterests)").monoLabel(10, color: FAMColor.textMuted, tracking: 1)
            }
            Text("Shown on your profile. Tap one to remove it, or add your own \u{2014} up to \(maxInterests).")
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.textMuted)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 6)
                .padding(.bottom, 9)
            FlowRow(spacing: 7) {
                ForEach(options, id: \.id) { row in
                    interestChip(row, full: full)
                }
            }
            HStack(spacing: 8) {
                TextField("", text: $adding,
                          prompt: Text("Add any topic \u{2014} e.g. Surfing, AI, Jazz").foregroundColor(FAMColor.textFaint))
                    .font(FAMFont.body(12))
                    .foregroundStyle(FAMColor.text)
                    .submitLabel(.done)
                    .onSubmit { addTyped() }
                    .padding(.horizontal, 13)
                    .frame(minHeight: 36)
                    .overlay(Capsule().stroke(FAMColor.textFaint, style: StrokeStyle(lineWidth: 1, dash: [3, 3])))
                Button { addTyped() } label: {
                    WebIcon(path: Icons.plus, size: 17)
                        .foregroundStyle(FAMColor.ink)
                        .frame(width: 36, height: 36)
                        .background(FAMColor.paper, in: Circle())
                }
                .buttonStyle(.press)
                .accessibilityLabel("Add this topic")
            }
            .padding(.top, 10)
        }
        .padding(.bottom, 16)
    }

    /// `.yf-int`: copper with a tick when chosen; with five chosen the rest
    /// are dim and do nothing.
    private func interestChip(_ row: InterestOption, full: Bool) -> some View {
        let on = picked.contains(row.id)
        let dim = !on && full
        return Button { toggle(row) } label: {
            HStack(spacing: 5) {
                if on { WebIcon(path: YouIcons.check, size: 13) }
                Text(row.label)
                    .font(FAMFont.body(12, on ? .bold : .regular))
                    .lineLimit(1)
            }
            .foregroundStyle(on ? FAMColor.ink : (dim ? Color(hex: 0x77718A) : FAMColor.textMuted))
            .padding(.horizontal, 12)
            .frame(minHeight: 30)
            .background(on ? FAMColor.copper : Color.clear, in: Capsule())
            .overlay(Capsule().stroke(on ? FAMColor.copper : FAMColor.borderStrong))
            .opacity(dim ? 0.6 : 1)
        }
        .buttonStyle(.press)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private func toggle(_ row: InterestOption) {
        if let at = picked.firstIndex(of: row.id) {
            picked.remove(at: at)
        } else if picked.count >= maxInterests {
            return
        } else {
            picked.append(row.id)
        }
        dirty = true
    }

    /// Typed text joins the options and is chosen if there is room; it is
    /// added to the stored topics on save, never replacing them.
    private func addTyped() {
        let words = adding.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        let text = String(words.prefix(60))
        guard !text.isEmpty else { return }
        adding = ""
        let existing = options.first { $0.label.lowercased() == text.lowercased() }
        let row = existing ?? InterestOption(id: text, label: text)
        if existing == nil {
            options.append(row)
            typed.append(text)
        }
        if !picked.contains(row.id) && picked.count < maxInterests { picked.append(row.id) }
        dirty = true
    }

    // MARK: Where you are

    private var placeBlock: some View {
        VStack(alignment: .leading, spacing: 0) {
            FieldLabel(text: "Where you are")
            TextField("", text: $city, prompt: Text("City").foregroundColor(FAMColor.textFaint))
                .textContentType(.addressCity)
                .youInput()
                .padding(.bottom, 8)
            HStack(spacing: 8) {
                TextField("", text: $region, prompt: Text("State or region").foregroundColor(FAMColor.textFaint))
                    .textContentType(.addressState)
                    .youInput()
                TextField("", text: $country, prompt: Text("Country").foregroundColor(FAMColor.textFaint))
                    .textContentType(.countryName)
                    .youInput()
            }
            Text("Optional. For local episodes \u{2014} and it shows on your profile.")
                .font(FAMFont.body(10.5))
                .foregroundStyle(FAMColor.textFaint)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 7)
        }
        .padding(.bottom, 13)
    }

    // MARK: Account (edit only)

    private var accountBlock: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Account").monoLabel(10, color: FAMColor.copper, tracking: 1.6)
                .padding(.top, 22)
                .padding(.bottom, 8)
            SetRow(label: "Signed in as", value: model.listener?.email ?? "")
            SetRow(label: PasswordChange.label(you.account)) {
                PasswordChange.open(model: model, account: you.account, ask: $textAsk)
            }
        }
    }

    // MARK: The dock

    private var dock: some View {
        EntryDock {
            EntryButton(label: editing ? "Save" : "Next", working: saving) { save() }
            if !editing {
                let missing = name.trimmingCharacters(in: .whitespaces).isEmpty
                Button { skip() } label: {
                    Text("Skip for now")
                        .monoLabel(10.5, color: missing ? FAMColor.textFaint : FAMColor.textMuted, tracking: 0.63)
                        .opacity(missing ? 0.55 : 1)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 14)
                        .padding(.bottom, 2)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.press)
            }
        }
    }

    // MARK: Filling and saving

    private func fill() async {
        guard !filled else { return }
        filled = true
        if you.profile == nil { await you.loadProfile() }
        await you.loadPrefs()
        let p = you.profile
        name = p?.name ?? ""
        handle = p?.handle ?? ""
        let place = you.prefs?.location
        city = place?.city ?? ""
        region = place?.region ?? ""
        country = place?.country ?? ""
        if !editing && name.isEmpty {
            await you.loadAccount()
            name = you.account?.displayName ?? ""
        }
        var seen: Set<String> = []
        var rows: [InterestOption] = []
        let shown = p?.interestsShown ?? []
        let ranked = p?.interestsRanked ?? []
        for i in shown + ranked where !seen.contains(i.id) {
            seen.insert(i.id)
            rows.append(InterestOption(id: i.id, label: i.label ?? i.id))
        }
        for f in you.prefs?.interestsAll ?? [] where !seen.contains(f.id) {
            seen.insert(f.id)
            rows.append(InterestOption(id: f.id, label: f.label ?? f.id))
        }
        options = rows
        picked = Array(shown.map(\.id).prefix(maxInterests))
        if !editing {
            try? await Task.sleep(nanoseconds: 80_000_000)
            focus = .name
        }
    }

    private func save() {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let h = Self.cleanHandle(handle)
        handleBad = false
        handleNote = Self.handleHelp
        if !editing && n.isEmpty {
            nameMissing = true
            asking = true
            return
        }
        if n.isEmpty {
            model.nowPlaying.say("A name, please")
            return
        }
        if h.count < 3 {
            handleNote = "A handle needs at least three characters."
            handleBad = true
            return
        }
        saving = true
        let place: [String: Any] = [
            "city": city.trimmingCharacters(in: .whitespacesAndNewlines),
            "region": region.trimmingCharacters(in: .whitespacesAndNewlines),
            "country": country.trimmingCharacters(in: .whitespacesAndNewlines),
        ]
        // The place beside the name, never in its way (`saveIdentity` sends
        // both at once); its failure is said here, not swallowed (Q12).
        let prefs = you
        let placeTask = Task { () -> Bool in await prefs.savePreference(place) }
        Task {
            do {
                try await model.api.setMe(name: n, handle: h, avatar: nil)
            } catch {
                saving = false
                let sentence = SocialModel.sentence(error, else: "Could not save that")
                if sentence.lowercased().contains("taken") {
                    handleNote = sentence
                    handleBad = true
                } else {
                    model.nowPlaying.say(sentence)
                }
                return
            }
            if editing { await saveInterests() }
            let placeSaved = await placeTask.value
            await you.loadProfile()
            you.meChanged()
            saving = false
            if !placeSaved {
                model.nowPlaying.say("Could not save where you are")
            } else if editing {
                model.nowPlaying.say("Saved")
            }
            if editing {
                dismiss()
            } else {
                model.entry = .interests
            }
        }
    }

    /// Written only if touched (`identityIntDirty`): the chips shown, and
    /// typed topics added to the stored ones - never in place of them; if
    /// the stored list cannot be read, typed ones are shown but not added.
    private func saveInterests() async {
        guard dirty else { return }
        var body: [String: Any] = ["profile_interests": picked]
        if !typed.isEmpty {
            var kept = you.prefs?.topics
            if kept == nil { kept = (try? await model.api.preferences())?.topics }
            if let kept {
                let have = Set(kept.map { $0.lowercased() })
                body["topics"] = kept + typed.filter { !have.contains($0.lowercased()) }
            }
        }
        _ = await you.savePreference(body)
    }

    /// "Skip for now": refused while the name is missing; otherwise the
    /// name goes on the account and the interests come next.
    private func skip() {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        if n.isEmpty {
            nameMissing = true
            asking = true
            return
        }
        Task {
            // A failed save is said and the step stays (`skipIdentity`).
            do {
                try await model.api.updateAccount(["display_name": n])
            } catch {
                model.nowPlaying.say(SocialModel.sentence(error, else: "Could not save that"))
                return
            }
            model.entry = .interests
        }
    }

    private func closeRequired() {
        asking = false
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 100_000_000)
            focus = .name
        }
    }

    /// On this screen the picture is saved with the fields' name and handle.
    private func savePicture(_ url: String) {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let h = Self.cleanHandle(handle)
        guard !n.isEmpty, h.count >= 3 else {
            handleNote = "A name and a handle first \u{2014} then the photo."
            handleBad = true
            return
        }
        Task {
            if let refusal = await you.saveAvatar(url, name: n, handle: h) {
                model.nowPlaying.say(refusal)
            } else {
                model.nowPlaying.say("Picture saved")
            }
        }
    }
}

/// "Please fill out required fields before continuing" (`#identityReqPop`).
struct RequiredPopup: View {
    let close: () -> Void

    var body: some View {
        ZStack {
            Color(hex: 0x0A0810).opacity(0.55)
                .ignoresSafeArea()
                .onTapGesture { close() }
            Text("Please fill out required fields before continuing")
                .font(FAMFont.body(15))
                .foregroundStyle(FAMColor.text)
                .lineSpacing(6)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 28)
                .padding(.leading, 20)
                .padding(.trailing, 46)
                .padding(.bottom, 24)
                .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 16))
                .overlay(RoundedRectangle(cornerRadius: 16).stroke(FAMColor.borderStrong))
                .overlay(alignment: .topTrailing) {
                    Button(action: close) {
                        WebIcon(path: Icons.close, size: 14, line: 2.4)
                            .foregroundStyle(FAMColor.textMuted)
                            .frame(width: 36, height: 36)
                    }
                    .buttonStyle(.press)
                    .padding(8)
                    .accessibilityLabel("Close")
                }
                .shadow(color: .black.opacity(0.45), radius: 20, y: 18)
                .frame(maxWidth: 340)
                .padding(.horizontal, 16)
        }
        .transition(.opacity)
    }
}

// MARK: - Changing the password (`changePassword`)

@MainActor
enum PasswordChange {
    /// The row's words (§571, the web's `passwordSettingsRow`): "Set a
    /// password" whenever `/api/account` says there is none; otherwise, or
    /// before the answer is in, "Change password".
    static func label(_ account: AccountInfo?) -> String {
        account?.hasPassword == false ? "Set a password" : "Change password"
    }

    /// The row's tap: the first-password flow directly when there is none,
    /// else `start`, which asks the server before choosing.
    static func open(model: AppModel, account: AccountInfo?, ask: Binding<TextAsk?>) {
        if account?.hasPassword == false {
            askFirst(model: model, ask: ask)
        } else {
            start(model: model, ask: ask)
        }
    }

    /// "Set a password" (`setFirstPassword`).
    static func askFirst(model: AppModel, ask: Binding<TextAsk?>) {
        ask.wrappedValue = TextAsk(title: "Set a password", placeholder: "at least 8 characters",
                                   button: "Set", secure: true) { new in
            PasswordChange.setFirst(model: model, new)
        }
    }

    /// An account with no password sets a first one; otherwise the current
    /// one, then the new - and every device is signed out, this one too,
    /// as on the web.
    static func start(model: AppModel, ask: Binding<TextAsk?>) {
        Task {
            let info = try? await model.api.account()
            if info?.hasPassword == false {
                askFirst(model: model, ask: ask)
            } else {
                ask.wrappedValue = TextAsk(title: "Current password", placeholder: "",
                                           button: "Next", secure: true) { current in
                    ask.wrappedValue = TextAsk(title: "New password", placeholder: "at least 8 characters",
                                               button: "Change", secure: true) { new in
                        PasswordChange.change(model: model, current: current, new: new)
                    }
                }
            }
        }
    }

    private static func setFirst(model: AppModel, _ new: String) {
        guard new.count >= 8 else {
            model.nowPlaying.say("Use at least 8 characters for your password.")
            return
        }
        guard new.count <= 1024 else {
            model.nowPlaying.say("That password is too long.")
            return
        }
        Task {
            do {
                try await model.api.setPassword(new)
                model.nowPlaying.say("Password set")
                await model.you.loadAccount()
            } catch {
                model.nowPlaying.say(SocialModel.sentence(error, else: "That did not work"))
            }
        }
    }

    private static func change(model: AppModel, current: String, new: String) {
        Task {
            do {
                // The web's words (`askCurrentPassword`); the server has
                // signed this device out too, so it lands on the front door.
                let still = try await model.changePassword(current: current, new: new)
                model.nowPlaying.say("Password changed \u{2014} other devices signed out")
                if still { await model.you.loadAccount() }
            } catch {
                model.nowPlaying.say(SocialModel.sentence(error, else: "That did not work"))
            }
        }
    }
}

// MARK: - Where you are (`screen-location`)

/// Its own screen with an X and Save, opened from Settings, going back to
/// where it was opened from. The web's words, the note under Country
/// included (the owner's call over Q6).
struct LocationView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var you: YouModel
    @Environment(\.dismiss) private var dismiss
    @State private var city = ""
    @State private var region = ""
    @State private var country = ""
    @State private var saving = false

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Spacer()
                CloseCircle(label: "Close") { dismiss() }
            }
            .padding(.horizontal, 16)
            .padding(.top, 14)
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Where you are")
                        .font(FAMFont.heading(20, .semibold))
                        .foregroundStyle(FAMColor.text)
                    Text("So we can offer you episodes about where you live. It shows on your profile.")
                        .font(FAMFont.body(13))
                        .foregroundStyle(FAMColor.textMuted)
                        .lineSpacing(5)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 14)
                        .padding(.bottom, 26)
                    field("City", "City", $city, .addressCity)
                    field("State or region", "State or region", $region, .addressState)
                    field("Country", "Country", $country, .countryName)
                    // The web's note, word for word (`#locNote`, the owner's call).
                    Text("Only you can see this.")
                        .font(FAMFont.body(10.5))
                        .foregroundStyle(FAMColor.textFaint)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 40)
                .padding(.top, 18)
            }
            .scrollDismissesKeyboard(.interactively)
            EntryDock {
                EntryButton(label: "Save", working: saving) { save() }
            }
        }
        .background(FAMColor.background.ignoresSafeArea())
        .overlay(alignment: .bottom) { ToastView().padding(.bottom, 96) }
        .task {
            await you.loadPrefs()
            let place = you.prefs?.location
            city = place?.city ?? ""
            region = place?.region ?? ""
            country = place?.country ?? ""
        }
    }

    private func field(_ label: String, _ hint: String, _ text: Binding<String>,
                       _ kind: UITextContentType) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            FieldLabel(text: label)
            TextField("", text: text, prompt: Text(hint).foregroundColor(FAMColor.textFaint))
                .textContentType(kind)
                .youInput()
        }
        .padding(.bottom, 13)
    }

    private func save() {
        saving = true
        let body: [String: Any] = [
            "city": city.trimmingCharacters(in: .whitespacesAndNewlines),
            "region": region.trimmingCharacters(in: .whitespacesAndNewlines),
            "country": country.trimmingCharacters(in: .whitespacesAndNewlines),
        ]
        Task {
            let ok = await you.savePreference(body)
            saving = false
            model.nowPlaying.say(ok ? "Saved" : "Could not save that")
            // Closed either way, as the web's `saveLocation` closes it.
            dismiss()
            if ok { await you.loadProfile() }
        }
    }
}

// MARK: - Your interests (`screen-intro`: from Settings, and the first run)

/// A subject chosen: from the catalogue, or typed.
struct ChosenTopic: Hashable {
    let id: String
    let label: String
}

/// The whole list of topics (§142): search, A to Z, (+) / ✓, "Add <words>
/// as a topic"; facets chosen before the list replaced the wheel shown as
/// chips that can be taken off. From Settings: an X that keeps nothing and
/// Save. On the first run: "Start listening" and "Skip for now", then the app.
struct InterestsView: View {
    enum Mode { case settings, firstRun }

    let mode: Mode
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var you: YouModel
    @Environment(\.dismiss) private var dismiss
    @State private var facets: [String] = []
    @State private var chosen: [ChosenTopic] = []
    @State private var catalogue: [Subject] = []
    @State private var labels: [String: String] = [:]
    @State private var search = ""
    @State private var loaded = false
    @State private var failed = false
    @State private var saving = false

    private var fromSettings: Bool { mode == .settings }

    var body: some View {
        VStack(spacing: 0) {
            if fromSettings {
                HStack {
                    Spacer()
                    CloseCircle(label: "Back to Settings") { dismiss() }
                }
                .padding(.horizontal, 16)
                .padding(.top, 14)
            } else {
                StepMarks(step: 3)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Your interests")
                        .font(FAMFont.heading(20, .semibold))
                        .foregroundStyle(FAMColor.text)
                    if fromSettings {
                        Text("Tap a topic to add or remove it, or type your own.")
                            .font(FAMFont.body(11.5))
                            .foregroundStyle(FAMColor.textMuted)
                            .padding(.top, 8)
                    }
                    if !facets.isEmpty { facetChips }
                    TextField("", text: $search,
                              prompt: Text("Search topics, or type your own").foregroundColor(FAMColor.textFaint))
                        .textInputAutocapitalization(.never)
                        .submitLabel(.done)
                        .onSubmit { addTyped() }
                        .youInput()
                        .padding(.top, 14)
                        .padding(.bottom, 4)
                    Text(pickedLine)
                        .font(FAMFont.mono(9.5))
                        .tracking(0.5)
                        .foregroundStyle(FAMColor.copper)
                        .frame(minHeight: 12)
                        .padding(.top, 6)
                        .padding(.bottom, 2)
                    list
                    Text("Saved to your account, so they follow you to any device.")
                        .font(FAMFont.body(11.5))
                        .foregroundStyle(FAMColor.textFaint)
                        .padding(.top, 16)
                }
                .padding(.horizontal, 40)
                .padding(.top, fromSettings ? 18 : 30)
                .padding(.bottom, 24)
            }
            .scrollDismissesKeyboard(.interactively)
            dock
        }
        .background(FAMColor.background.ignoresSafeArea())
        .overlay(alignment: .bottom) { ToastView().padding(.bottom, 96) }
        .task { await load() }
    }

    private var pickedLine: String {
        let n = chosen.count + facets.count
        if n == 0 { return "" }
        return n == 1 ? "1 topic chosen" : "\(n) topics chosen"
    }

    /// `.intro-facet`: copper, 30 tall, the name and a small X.
    private var facetChips: some View {
        FlowRow(spacing: 7) {
            ForEach(sortedFacets, id: \.self) { tag in
                Button { facets.removeAll { $0 == tag } } label: {
                    HStack(spacing: 6) {
                        Text(labels[tag] ?? tag)
                            .font(FAMFont.body(12, .semibold))
                        WebIcon(path: Icons.close, size: 10, line: 3)
                    }
                    .foregroundStyle(FAMColor.ink)
                    .padding(.leading, 13)
                    .padding(.trailing, 11)
                    .frame(minHeight: 30)
                    .background(FAMColor.copper, in: Capsule())
                }
                .buttonStyle(.press)
                .accessibilityLabel("Remove " + (labels[tag] ?? tag))
            }
        }
        .padding(.top, 12)
    }

    private var sortedFacets: [String] {
        facets.sorted { (labels[$0] ?? $0).lowercased() < (labels[$1] ?? $1).lowercased() }
    }

    private var term: String { search.trimmingCharacters(in: .whitespacesAndNewlines) }

    private var shown: [Subject] {
        let t = term.lowercased()
        let rows = t.isEmpty ? catalogue : catalogue.filter { $0.label.lowercased().contains(t) }
        return rows.sorted { $0.label.lowercased() < $1.label.lowercased() }
    }

    private func isChosen(_ id: String) -> Bool {
        chosen.contains { $0.id.lowercased() == id.lowercased() || $0.label.lowercased() == id.lowercased() }
    }

    @ViewBuilder private var list: some View {
        let raw = term
        let exact = catalogue.contains { $0.label.lowercased() == raw.lowercased() }
        if !raw.isEmpty && !exact && !isChosen(raw) {
            Button { addTyped() } label: {
                HStack(spacing: 11) {
                    WebIcon(path: Icons.plus, size: 23, line: 1.6)
                        .foregroundStyle(FAMColor.textMuted)
                        .frame(width: 46, height: 46)
                        .overlay(RoundedRectangle(cornerRadius: 13)
                            .stroke(FAMColor.borderStrong, style: StrokeStyle(lineWidth: 1, dash: [3, 3])))
                    (Text("Add ") + Text(raw).bold().foregroundColor(FAMColor.text) + Text(" as a topic"))
                        .font(FAMFont.body(12.5))
                        .foregroundStyle(FAMColor.textMuted)
                        .lineLimit(2)
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 12)
                .padding(.horizontal, 4)
                .contentShape(Rectangle())
            }
            .buttonStyle(.press)
        }
        if !loaded {
            Text("Loading topics\u{2026}").font(FAMFont.body(11.5)).foregroundStyle(FAMColor.textFaint)
                .padding(.vertical, 18)
        } else if failed && catalogue.isEmpty {
            Text("Topics could not load. You can still type your own above.")
                .font(FAMFont.body(11.5)).foregroundStyle(FAMColor.textFaint)
                .padding(.vertical, 18)
        } else if shown.isEmpty {
            let tail = (!raw.isEmpty && !exact && !isChosen(raw))
                ? " \u{2014} add it above and it becomes one of yours." : "."
            // `term`: the typed words lower-cased (`raw.toLowerCase()`).
            Text("Nothing on the list matches \u{201C}" + raw.lowercased() + "\u{201D}" + tail)
                .font(FAMFont.body(11.5)).foregroundStyle(FAMColor.textFaint)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.vertical, 18)
        } else {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(shown) { s in row(s) }
            }
            Text("Pick a few to start. DailyFAM learns the rest from what you play.")
                .font(FAMFont.body(11.5)).foregroundStyle(FAMColor.textFaint)
                .padding(.top, 12)
        }
    }

    /// `.cat-row`: the 46 pt art (radius 13), the name Geist 14 bold, the
    /// 28 pt copper (+) - sage ✓ once chosen. The whole row toggles.
    private func row(_ s: Subject) -> some View {
        let on = chosen.contains { $0.id == s.id }
        return Button { toggle(s) } label: {
            HStack(spacing: 13) {
                SubjectIcon(name: s.icon ?? "news", size: 23)
                    .foregroundStyle(FAMColor.textMuted)
                    .frame(width: 46, height: 46)
                    .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 13))
                    .overlay(RoundedRectangle(cornerRadius: 13).stroke(FAMColor.border))
                Text(s.label)
                    .font(FAMFont.body(14, .bold))
                    .foregroundStyle(FAMColor.text)
                    .frame(maxWidth: .infinity, alignment: .leading)
                WebIcon(path: on ? YouIcons.check : Icons.plus, size: 13, line: 3)
                    .foregroundStyle(FAMColor.ink)
                    .frame(width: 28, height: 28)
                    .background(on ? FAMColor.sage : FAMColor.copper, in: Circle())
            }
            .padding(.vertical, 10)
            .padding(.horizontal, 2)
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityLabel(s.label)
        .accessibilityValue(on ? "Added" : "")
    }

    private func toggle(_ s: Subject) {
        if chosen.contains(where: { $0.id == s.id }) {
            chosen.removeAll { $0.id == s.id }
        } else {
            chosen.append(ChosenTopic(id: s.id, label: s.label))
        }
    }

    /// The search's words as a topic of their own; a catalogue name typed
    /// adds the catalogue's entry.
    private func addTyped() {
        let raw = term
        guard !raw.isEmpty else { return }
        let listed = catalogue.first { $0.label.lowercased() == raw.lowercased() }
        let id = listed?.id ?? raw
        if !chosen.contains(where: { $0.id.lowercased() == id.lowercased() }) {
            chosen.append(ChosenTopic(id: id, label: listed?.label ?? raw))
        }
        search = ""
    }

    private var dock: some View {
        EntryDock {
            EntryButton(label: fromSettings ? "Save" : "Start listening", working: saving) { save() }
            if !fromSettings {
                Button { skip() } label: {
                    Text("Skip for now")
                        .monoLabel(10.5, color: FAMColor.textMuted, tracking: 0.63)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 14)
                        .padding(.bottom, 2)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.press)
            }
        }
    }

    /// Read again rather than trusted: this is an editor, and saving what
    /// it last happened to hold would overwrite a change from another device.
    private func load() async {
        guard !loaded else { return }
        do {
            let prefs = try await model.api.preferences()
            facets = prefs.interests ?? []
            var named: [String: String] = [:]
            for f in prefs.interestsAll ?? [] { named[f.id] = f.label ?? f.id }
            labels = named
            catalogue = prefs.catalogue ?? []
            chosen = (prefs.topicsChosen ?? []).map { ChosenTopic(id: $0.id, label: $0.label ?? $0.id) }
        } catch {
            failed = true
        }
        loaded = true
    }

    private func save() {
        saving = true
        var body: [String: Any] = ["interests": facets, "topics": chosen.map(\.id)]
        if !fromSettings { body["intro_done"] = true }
        Task {
            let ok = await you.savePreference(body)
            saving = false
            if fromSettings {
                model.nowPlaying.say(ok ? "Saved" : "Could not save that")
                if ok {
                    await you.loadProfile()
                    dismiss()
                }
            } else {
                model.finishEntry()
            }
        }
    }

    /// Choosing nothing is an answer: the list cleared, then the app.
    private func skip() {
        Task {
            _ = await you.savePreference(["interests": [String](), "topics": [String](), "intro_done": true])
            model.finishEntry()
        }
    }
}
