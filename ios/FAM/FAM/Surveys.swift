import SwiftUI

/// The optional surveys (`surveys.py`, PROBLEMS.md §560), as the web draws
/// them (`static/index.html`: `offerEpisodeSurvey`, `maybeOfferAppSurvey`,
/// `drawSurvey`, `sendSurvey`, `surveyNotNow`).
///
/// The server decides which survey, if any, and when: `offer` is asked only
/// at a moment one can be shown (an answer there counts as offered), and
/// only for an account. Every survey has Not now; nothing waits on one; an
/// answer is never a taste signal.
///
/// Two places: the episode check on the What's next card (`pulse`), and the
/// day-3 check-in and two-week wrap-up, offered as a small invite above the
/// bars (`invite`) that opens the survey in a sheet (`sheetOpen`).
@MainActor
final class SurveysModel: ObservableObject {
    /// One question's answer: an option id, several, or a number, plus the
    /// "Other" box or an option's own box (`text`).
    struct Answer: Equatable {
        var pick: String?
        var picks: [String] = []
        var number: Int?
        var other = false
        var text = ""
    }

    /// The episode check on the What's next card.
    @Published private(set) var pulse: Survey?
    /// "Thanks - that helps." where the episode check was.
    @Published private(set) var pulseThanked = false
    /// The day-3 or two-week survey, offered and not yet started.
    @Published private(set) var invite: Survey?
    /// The same survey, open in its sheet.
    @Published var sheet: Survey?
    @Published private(set) var answers: [String: Answer] = [:]
    @Published private(set) var problem = ""
    @Published private(set) var sending = false

    private let api: FAMAPI
    /// The toast (`toast`), wired by `AppModel`: a sent app survey says
    /// "Thanks — that helps" as the web's `sendSurvey` does.
    var say: (String) -> Void = { _ in }
    private var episodeID = ""
    private var episodeTitle = ""
    /// Once in half an hour from this device; the server keeps the real
    /// limit (once a day, three Not nows).
    private var askedAt: Date?
    static let askEvery: TimeInterval = 30 * 60

    init(api: FAMAPI) {
        self.api = api
    }

    /// Whichever survey is being answered.
    var current: Survey? { pulse ?? sheet }

    // MARK: - Offering

    /// The What's next card is up: the episode check, if the server says so.
    func offerEpisode(id: String, title: String, signedIn: Bool) async {
        // An app survey offered and not yet answered keeps its place.
        guard signedIn, pulse == nil, sheet == nil, invite == nil else { return }
        guard let survey = try? await api.offerSurvey(moment: "episode_end"),
              survey.id == "episode" else { return }
        // The card went while the server answered (the web checks that
        // What's next is still up), or an app survey arrived meanwhile.
        guard !Task.isCancelled, pulse == nil, sheet == nil, invite == nil else { return }
        episodeID = id
        episodeTitle = title
        answers = [:]
        problem = ""
        pulseThanked = false
        pulse = survey
    }

    /// The card went: so does its survey (offered, not answered - the
    /// server already counted it).
    func clearPulse() {
        pulse = nil
        pulseThanked = false
        if sheet == nil { answers = [:] }
    }

    /// The app is in front with nothing playing and nothing open
    /// (`surveyMomentFree`): the day-3 or two-week survey, if one is due.
    func offerOnOpen(free: Bool) async {
        guard free, invite == nil, sheet == nil, pulse == nil else { return }
        if let at = askedAt, Date().timeIntervalSince(at) < Self.askEvery { return }
        askedAt = Date()
        guard let survey = try? await api.offerSurvey(moment: "open"),
              survey.id != "episode" else { return }
        invite = survey
    }

    func start() {
        guard let survey = invite else { return }
        invite = nil
        answers = [:]
        problem = ""
        sheet = survey
    }

    /// "Not now" (and the X): offered again a day later, at most three times.
    func notNow() {
        guard let survey = current ?? invite else { return }
        pulse = nil
        invite = nil
        sheet = nil
        answers = [:]
        Task { await api.dismissSurvey(survey.id) }
    }

    // MARK: - Answering

    func answer(_ id: String) -> Answer { answers[id] ?? Answer() }

    /// Shown, given what has been picked (`show_if`).
    func visible(_ q: SurveyQuestion) -> Bool {
        guard let rule = q.showIf else { return true }
        guard let pick = answers[rule.question]?.pick else { return false }
        return rule.in.contains(pick)
    }

    func questions(_ survey: Survey) -> [SurveyQuestion] {
        survey.questions.filter(visible)
    }

    /// Whether the question's box is showing (`surveyTextWanted`).
    func wantsText(_ q: SurveyQuestion) -> Bool {
        if q.kind == "text" { return true }
        let a = answer(q.id)
        if a.other { return true }
        let picked = q.kind == "multi" ? a.picks : (a.pick.map { [$0] } ?? [])
        return picked.contains { q.detail?[$0] != nil }
    }

    func textPrompt(_ q: SurveyQuestion) -> String {
        if q.kind == "text" { return q.placeholder ?? "" }
        let a = answer(q.id)
        let picked = q.kind == "multi" ? a.picks : (a.pick.map { [$0] } ?? [])
        var asked = picked.compactMap { q.detail?[$0] }
        if a.other { asked.append(asked.isEmpty ? "Tell us more" : "Anything else?") }
        return asked.joined(separator: " ")
    }

    /// A chip pressed (`surveyTap`).
    func tap(_ q: SurveyQuestion, option: String) {
        var a = answer(q.id)
        if q.kind == "multi" {
            if let at = a.picks.firstIndex(of: option) { a.picks.remove(at: at) } else { a.picks.append(option) }
        } else {
            a.pick = a.pick == option ? nil : option
            if a.pick != nil { a.other = false }
        }
        answers[q.id] = a
        problem = ""
        // The episode check's first tap: "Loved it" is the whole answer.
        if let survey = pulse, survey.questions.first?.id == q.id,
           questions(survey).count == 1, a.pick != nil {
            Task { await send() }
        }
    }

    func tapOther(_ q: SurveyQuestion) {
        var a = answer(q.id)
        a.other.toggle()
        // Other off with no option's own box showing: its words go too
        // (`surveyTap`).
        if !a.other {
            let picked = q.kind == "multi" ? a.picks : (a.pick.map { [$0] } ?? [])
            if !picked.contains(where: { q.detail?[$0] != nil }) { a.text = "" }
        }
        if q.kind == "single" && a.other { a.pick = nil }
        answers[q.id] = a
        problem = ""
    }

    func tapNumber(_ q: SurveyQuestion, _ n: Int) {
        var a = answer(q.id)
        a.number = a.number == n ? nil : n
        answers[q.id] = a
        problem = ""
    }

    func setText(_ q: SurveyQuestion, _ text: String) {
        var a = answer(q.id)
        a.text = String(text.prefix(500))
        answers[q.id] = a
    }

    /// What is sent: only questions showing, only boxes showing
    /// (`surveyPayload`).
    func payload(_ survey: Survey) -> [String: Any] {
        var out: [String: Any] = [:]
        for q in questions(survey) {
            guard let a = answers[q.id] else { continue }
            var entry: [String: Any] = [:]
            switch q.kind {
            case "multi": if !a.picks.isEmpty { entry["pick"] = a.picks }
            case "scale": if let n = a.number { entry["pick"] = n }
            case "single": if let p = a.pick { entry["pick"] = p }
            default: break
            }
            let text = a.text.trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty && wantsText(q) { entry["text"] = text }
            if !entry.isEmpty { out[q.id] = entry }
        }
        return out
    }

    func send() async {
        guard let survey = current, !sending else { return }
        let body = payload(survey)
        guard !body.isEmpty else {
            problem = "Pick at least one answer, or tap Not now."
            return
        }
        sending = true
        defer { sending = false }
        do {
            try await api.answerSurvey(survey.id, answers: body,
                                       episodeID: survey.id == "episode" ? episodeID : "",
                                       episodeTitle: survey.id == "episode" ? episodeTitle : "")
            problem = ""
            if pulse != nil {
                pulseThanked = true
            } else {
                sheet = nil
                say("Thanks \u{2014} that helps")
            }
            answers = [:]
        } catch FAMError.refused(_, let message) where !message.isEmpty {
            problem = message
        } catch {
            problem = "That did not send - try again."
        }
    }
}

// MARK: - Drawing one question

/// A question, its chips (or 1-10, or a line) and its box, as `.svy-q`.
struct SurveyQuestionView: View {
    @ObservedObject var surveys: SurveysModel
    let question: SurveyQuestion
    var showsText = true

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            if showsText {
                Text(question.text)
                    .font(FAMFont.body(13, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .fixedSize(horizontal: false, vertical: true)
            }
            switch question.kind {
            case "scale": scale
            case "text": EmptyView()
            default: chips
            }
            if surveys.wantsText(question) {
                TextField("", text: Binding(get: { surveys.answer(question.id).text },
                                            set: { surveys.setText(question, $0) }),
                          prompt: Text(surveys.textPrompt(question)).foregroundColor(FAMColor.textFaint))
                    .font(FAMFont.body(16))
                    .foregroundStyle(FAMColor.text)
                    .submitLabel(.done)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
            }
        }
    }

    private var chips: some View {
        let a = surveys.answer(question.id)
        return FlowRow(spacing: 6) {
            ForEach(question.options ?? []) { option in
                let on = question.kind == "multi" ? a.picks.contains(option.id) : a.pick == option.id
                chip(option.label, on: on) { surveys.tap(question, option: option.id) }
            }
            if question.other == true {
                chip("Other", on: a.other) { surveys.tapOther(question) }
            }
        }
    }

    private var scale: some View {
        let low = question.min ?? 1
        let high = max(low, question.max ?? 10)
        let picked = surveys.answer(question.id).number
        return VStack(spacing: 4) {
            HStack(spacing: 4) {
                ForEach(low...high, id: \.self) { n in
                    Button { surveys.tapNumber(question, n) } label: {
                        Text("\(n)")
                            .font(FAMFont.mono(12, bold: picked == n))
                            .foregroundStyle(picked == n ? FAMColor.ink : FAMColor.text)
                            .frame(maxWidth: .infinity, minHeight: 32)
                            .background(picked == n ? FAMColor.copper : FAMColor.surface, in: Capsule())
                            .overlay(Capsule().stroke(picked == n ? FAMColor.copper : FAMColor.borderStrong))
                    }
                    .buttonStyle(.press)
                    .accessibilityAddTraits(picked == n ? .isSelected : [])
                }
            }
            HStack {
                Text(question.low ?? "").monoLabel(9, color: FAMColor.textFaint)
                Spacer()
                Text(question.high ?? "").monoLabel(9, color: FAMColor.textFaint)
            }
        }
    }

    private func chip(_ label: String, on: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(FAMFont.body(12, on ? .bold : .medium))
                .foregroundStyle(on ? FAMColor.ink : FAMColor.text)
                .multilineTextAlignment(.leading)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .frame(minHeight: 32)
                .background(on ? FAMColor.copper : FAMColor.surface, in: Capsule())
                .overlay(Capsule().stroke(on ? FAMColor.copper : FAMColor.borderStrong))
        }
        .buttonStyle(.press)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

// MARK: - The episode check, on What's next (`#surveyPulse`)

struct SurveyPulse: View {
    @ObservedObject var surveys: SurveysModel
    /// Touching it pauses the countdown, as the search box does.
    let onTouch: () -> Void

    var body: some View {
        if let survey = surveys.pulse {
            VStack(alignment: .leading, spacing: 0) {
                if surveys.pulseThanked {
                    Text("Thanks \u{2014} that helps.")
                        .font(FAMFont.body(13))
                        .foregroundStyle(FAMColor.textMuted)
                } else {
                    HStack(alignment: .center, spacing: 8) {
                        Text(survey.title)
                            .font(FAMFont.heading(14, .semibold))
                            .foregroundStyle(FAMColor.text)
                        Spacer(minLength: 0)
                        closeButton
                    }
                    let shown = surveys.questions(survey)
                    VStack(alignment: .leading, spacing: 12) {
                        ForEach(Array(shown.enumerated()), id: \.element.id) { index, q in
                            // The first question is the title above it.
                            SurveyQuestionView(surveys: surveys, question: q, showsText: index > 0)
                        }
                    }
                    .padding(.top, 9)
                    if !surveys.problem.isEmpty {
                        Text(surveys.problem)
                            .font(FAMFont.body(12))
                            .foregroundStyle(FAMColor.copper)
                            .padding(.top, 10)
                    }
                    if shown.count > 1 {
                        HStack {
                            Spacer()
                            sendButton
                        }
                        .padding(.top, 12)
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 11)
            .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(FAMColor.border))
            .simultaneousGesture(TapGesture().onEnded { onTouch() })
            .onChange(of: surveys.answers) { _, _ in onTouch() }
            .padding(.bottom, 12)
        }
    }

    private var closeButton: some View {
        Button { surveys.notNow() } label: {
            WebIcon(path: Icons.close, size: 14, line: 2.4)
                .foregroundStyle(FAMColor.textFaint)
                .frame(width: 44, height: 44)
        }
        .buttonStyle(.press)
        .padding(-12)
        .accessibilityLabel("Not now")
    }

    private var sendButton: some View {
        Button { Task { await surveys.send() } } label: {
            Text("Send")
                .font(FAMFont.body(12, .bold))
                .foregroundStyle(FAMColor.ink)
                .padding(.horizontal, 16)
                .padding(.vertical, 9)
                .background(FAMColor.copper, in: Capsule())
        }
        .buttonStyle(.press)
        .disabled(surveys.sending)
    }
}

// MARK: - The invite above the bars (`#surveyInvite`)

struct SurveyInvite: View {
    @ObservedObject var surveys: SurveysModel

    var body: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 0)
            card
        }
        .animation(.easeOut(duration: 0.2), value: surveys.invite)
    }

    @ViewBuilder private var card: some View {
        if let survey = surveys.invite {
            VStack(alignment: .leading, spacing: 0) {
                Text(survey.title)
                    .font(FAMFont.heading(15, .semibold))
                    .foregroundStyle(FAMColor.text)
                if !survey.intro.isEmpty {
                    Text(survey.intro)
                        .font(FAMFont.body(12.5))
                        .foregroundStyle(FAMColor.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 3)
                }
                HStack(spacing: 8) {
                    Spacer()
                    Button("Not now") { surveys.notNow() }
                        .font(FAMFont.body(12, .bold))
                        .foregroundStyle(FAMColor.textMuted)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 9)
                        .buttonStyle(.press)
                    Button { surveys.start() } label: {
                        Text("Start")
                            .font(FAMFont.body(12, .bold))
                            .foregroundStyle(FAMColor.ink)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 9)
                            .background(FAMColor.copper, in: Capsule())
                    }
                    .buttonStyle(.press)
                }
                .padding(.top, 10)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 13)
            .frame(maxWidth: 360, alignment: .leading)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(FAMColor.borderStrong))
            .shadow(color: .black.opacity(0.3), radius: 17, y: 16)
            .padding(.horizontal, 14)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }
}

// MARK: - The day-3 check-in and the two-week wrap-up (`#surveyOverlay`)

/// Carries the survey sheet for a screen that does not observe the surveys
/// itself (`MainTabs`). Swiped away is the X: Not now.
struct SurveySheetHost: View {
    @ObservedObject var surveys: SurveysModel

    var body: some View {
        Color.clear
            .famSheet(item: Binding(get: { surveys.sheet },
                                 set: { if $0 == nil && surveys.sheet != nil { surveys.notNow() } })) { survey in
                SurveySheet(surveys: surveys, survey: survey)
                    .famThemed()
            }
    }
}

struct SurveySheet: View {
    @ObservedObject var surveys: SurveysModel
    let survey: Survey

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center) {
                Text(survey.title)
                    .font(FAMFont.heading(19, .semibold))
                    .foregroundStyle(FAMColor.text)
                Spacer()
                Button { surveys.notNow() } label: {
                    WebIcon(path: Icons.close, size: 16, line: 2.4)
                        .foregroundStyle(FAMColor.textFaint)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.press)
                .accessibilityLabel("Not now")
            }
            .padding(.leading, 20)
            .padding(.trailing, 8)
            .padding(.top, 10)
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if !survey.intro.isEmpty {
                        Text(survey.intro)
                            .font(FAMFont.body(12.5))
                            .foregroundStyle(FAMColor.textMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(surveys.questions(survey)) { q in
                        SurveyQuestionView(surveys: surveys, question: q)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.bottom, 16)
            }
            .scrollDismissesKeyboard(.interactively)
            if !surveys.problem.isEmpty {
                Text(surveys.problem)
                    .font(FAMFont.body(12))
                    .foregroundStyle(FAMColor.copper)
                    .padding(.horizontal, 20)
                    .padding(.top, 8)
            }
            HStack(spacing: 8) {
                Spacer()
                Button("Not now") { surveys.notNow() }
                    .font(FAMFont.body(13, .bold))
                    .foregroundStyle(FAMColor.textMuted)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .buttonStyle(.press)
                Button { Task { await surveys.send() } } label: {
                    Text("Send")
                        .font(FAMFont.body(13, .bold))
                        .foregroundStyle(FAMColor.ink)
                        .padding(.horizontal, 20)
                        .padding(.vertical, 10)
                        .background(FAMColor.copper, in: Capsule())
                }
                .buttonStyle(.press)
                .disabled(surveys.sending)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
        }
        .background(FAMColor.surface.ignoresSafeArea())
    }
}
