import Foundation

/// FAM's own autocorrect behind the keyboard (`autocorrect-at-send`, §142,
/// §247), ported from the web's `acAsk` / `acCorrectQuestion` /
/// `acInstall`. `/api/spell` is deliberately cautious: it answers `nil` for
/// anything that could be a name, which is half of what people ask FAM about.
///
/// Two jobs:
/// * **As words are typed**: each finished word is asked about and a fix
///   replaces it in place; a backspace straight after a fix puts the typed
///   word back, and that word is never corrected again (`AC_REVERTED`).
/// * **At send**: every unseen word is asked about at once, waiting at most
///   700 ms (`AC_SEND_WAIT_MS`), and two strings come back - the question
///   keeps the words the listener put back, the title takes every fix (the
///   title never misspells).
///
/// Nothing here starts an episode, a brief or a search: it is a spell
/// lookup only (`no-typing-prefetch`).
@MainActor
final class Speller {

    private let api: FAMAPI
    /// "word" or "^word" (a sentence's first word) → the fix, or nil.
    private var cache: [String: String?] = [:]
    /// Words the listener put back with a backspace, lower-cased.
    private(set) var reverted: Set<String> = []

    init(api: FAMAPI) { self.api = api }

    typealias Word = SpellWords.Word

    private static func key(_ word: String, _ first: Bool) -> String {
        (first ? "^" : "") + word
    }

    static func words(in text: String) -> [Word] { SpellWords.words(in: text) }
    static func startsSentence(_ head: String) -> Bool { SpellWords.startsSentence(head) }

    /// The fix the server gave for a word, if it has been asked.
    func known(_ word: String, first: Bool) -> (asked: Bool, fix: String?) {
        let k = Self.key(word, first)
        guard let i = cache.index(forKey: k) else { return (false, nil) }
        return (true, cache[i].value)
    }

    /// `acAsk`: one word, answered from memory when it has been asked.
    /// Offline, or a server without the speller: nil, and the keyboard's own
    /// pass is still there.
    func ask(_ word: String, first: Bool) async -> String? {
        let k = Self.key(word, first)
        if let i = cache.index(forKey: k) { return cache[i].value }
        do {
            let answer = try await api.spell([word], first: [first])
            let fix: String? = answer.corrections.first ?? nil
            cache[k] = fix
            return fix
        } catch {
            // A server that answered and refused is remembered as no fix, as
            // the web's `r.ok ? … : null` does; offline is asked again.
            if error is FAMError { cache[k] = Optional<String>.none }
            return nil
        }
    }

    func noteReverted(_ word: String) {
        reverted.insert(word.lowercased())
    }

    func isReverted(_ word: String) -> Bool {
        reverted.contains(word.lowercased())
    }

    /// `acCorrectQuestion`: the question to ask and the title to show, after
    /// one request for every unseen word or 700 ms, whichever comes first.
    func correct(_ text: String) async -> (query: String, title: String) {
        let found = Self.words(in: text)
        let ask = found.filter { cache.index(forKey: Self.key($0.word, $0.first)) == nil }
        if !ask.isEmpty {
            requests += 1
            let mine = requests
            // Its answer is kept whenever it lands, so a later send finds it.
            Task { await self.fill(ask, request: mine) }
            let deadline = Date().addingTimeInterval(SpellWords.sendWait)
            while !answered.contains(mine) && Date() < deadline {
                try? await Task.sleep(nanoseconds: 40_000_000)
            }
            answered.remove(mine)
        }
        return apply(text, found)
    }

    private var requests = 0
    private var answered: Set<Int> = []

    private func fill(_ ask: [Word], request: Int) async {
        do {
            let answer = try await api.spell(ask.map { $0.word }, first: ask.map { $0.first })
            for (i, w) in ask.enumerated() {
                let fix: String? = i < answer.corrections.count ? answer.corrections[i] : nil
                cache[Self.key(w.word, w.first)] = fix
            }
        } catch {
            // Refused: every word asked is remembered as no fix (the web's
            // `fixes[i] || null` over a null answer); offline: nothing kept.
            if error is FAMError {
                for w in ask { cache[Self.key(w.word, w.first)] = Optional<String>.none }
            }
        }
        answered.insert(request)
    }

    /// `acApply`: `query` keeps any word the listener put back; `title`
    /// takes every correction.
    private func apply(_ text: String, _ found: [Word]) -> (query: String, title: String) {
        var query = text as NSString
        var title = text as NSString
        for w in found.reversed() {
            let entry = cache[Self.key(w.word, w.first)] ?? nil
            guard let fix = entry, fix != w.word else { continue }
            let range = NSRange(location: w.start, length: (w.word as NSString).length)
            title = title.replacingCharacters(in: range, with: fix) as NSString
            if !isReverted(w.word) {
                query = query.replacingCharacters(in: range, with: fix) as NSString
            }
        }
        return (query as String, title as String)
    }
}

/// A search's provisional title (`deriveTitleFromPrompt`): the first seven
/// words, each capitalised except the small ones (never the first), a word
/// the listener capitalised kept as typed, and "…" when there were more.
/// Free of any actor, so a decoded value can name itself with it.
enum SearchTitle {
    /// `TITLE_SMALL_WORDS`.
    static let small: Set<String> = [
        "a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
        "into", "is", "nor", "of", "on", "or", "over", "per", "the", "to", "up",
        "via", "vs", "with",
    ]

    static func from(_ text: String) -> String {
        let parts = text.trimmingCharacters(in: .whitespacesAndNewlines)
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
        var out: [String] = []
        for (i, w) in parts.prefix(7).enumerated() {
            let lower = w.lowercased()
            if i > 0 && small.contains(lower) && w == lower {
                out.append(lower)
            } else if w != lower {
                out.append(w)
            } else {
                out.append(String(w.prefix(1)).uppercased() + String(w.dropFirst()))
            }
        }
        var title = out.joined(separator: " ")
        if parts.count > 7 { title += "\u{2026}" }
        return title
    }
}

/// Finding the words the speller is asked about (`acWordsIn`): plain
/// functions of a string, free of any actor.
enum SpellWords {
    /// `AC_SEND_WAIT_MS`: the longest a send waits on the speller.
    static let sendWait: TimeInterval = 0.7
    /// `AC_PAUSE_MS`: a typing pause pre-asks the word being typed.
    static let pause: TimeInterval = 0.4

    /// One word found in a text: where it starts (UTF-16), and whether it
    /// starts a sentence, where a capital is the keyboard's own.
    struct Word {
        let word: String
        let start: Int
        let first: Bool
    }

    private static let wordPattern = try? NSRegularExpression(pattern: "[A-Za-z]+(?:'[A-Za-z]+)?")
    /// What may not sit just before a whole word: a letter or digit, or part
    /// of a handle, a hashtag, a path or an address.
    private static let beforeWord = try? NSRegularExpression(pattern: "[\\p{L}\\p{N}@#/._-]")
    private static let afterWord = try? NSRegularExpression(pattern: "[\\p{L}\\p{N}@#/_-]")
    private static let sentenceEnd = try? NSRegularExpression(pattern: "(^\\s*$)|([.!?\\n]\\s*$)")

    private static func matches(_ re: NSRegularExpression?, _ text: String) -> Bool {
        guard let re else { return false }
        let ns = text as NSString
        return re.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)) != nil
    }

    static func startsSentence(_ head: String) -> Bool {
        matches(sentenceEnd, head)
    }

    /// `acWordsIn`: every whole word the word-by-word pass would have asked
    /// about, at most 40.
    static func words(in text: String) -> [Word] {
        guard let re = wordPattern else { return [] }
        let ns = text as NSString
        var out: [Word] = []
        let found = re.matches(in: text, range: NSRange(location: 0, length: ns.length))
        for m in found {
            let start = m.range.location
            let end = start + m.range.length
            if start > 0 {
                let before = ns.substring(with: NSRange(location: start - 1, length: 1))
                if matches(beforeWord, before) { continue }
            }
            if end < ns.length {
                let after = ns.substring(with: NSRange(location: end, length: 1))
                if matches(afterWord, after) { continue }
            }
            let head = ns.substring(to: start)
            out.append(Word(word: ns.substring(with: m.range), start: start, first: startsSentence(head)))
            if out.count >= 40 { break }
        }
        return out
    }

}

/// The word-by-word pass (`acInstall`) for a box other than searchFAM's -
/// the message box (`#threadInput` carries `data-autocorrect`). The same
/// rules as searchFAM's own: a word just finished at the end (a space, a
/// new line or punctuation typed after it) is asked about and fixed in
/// place, only if it is still there untouched; a whole word only (not part
/// of a handle, a hashtag, a path or an address); a sentence's first word
/// asked as one; one backspace straight after a fix puts the typed word
/// back, and that word is never corrected again. No send-time pass: the web
/// sends a message as it stands.
@MainActor
final class LiveCorrector {
    private let speller: Speller
    private var lastFix: WordFix?
    /// The text this corrector wrote itself, so its own change is not taken
    /// for typing.
    private var ownChange: String?

    init(speller: Speller) { self.speller = speller }

    /// The box was cleared or sent (`acLast = null`).
    func reset() {
        lastFix = nil
        ownChange = nil
    }

    private static let boundary = CharacterSet(charactersIn: " \t\n.,!?;:)")
    private static let lastWord = try? NSRegularExpression(pattern: "([A-Za-z]+(?:'[A-Za-z]+)?)$")
    private static let partOfWord = try? NSRegularExpression(pattern: "[\\p{L}\\p{N}@#/._-]")

    private static func isBoundary(_ text: String) -> Bool {
        guard let scalar = text.unicodeScalars.first else { return false }
        return boundary.contains(scalar)
    }

    /// Every change to the box. `read` is what the box holds now (the fix
    /// lands only if the word is still where it was); `write` puts text in.
    func changed(from old: String, to now: String,
                 read: @escaping () -> String, write: @escaping (String) -> Void) {
        if let mine = ownChange, mine == now {
            ownChange = nil
            return
        }
        ownChange = nil
        let oldNS = old as NSString
        let nowNS = now as NSString
        if let fix = lastFix, revert(fix, oldNS, nowNS, write: write) { return }
        lastFix = nil
        guard nowNS.length == oldNS.length + 1, now.hasPrefix(old) else { return }
        let typedChar = nowNS.substring(from: nowNS.length - 1)
        guard Self.isBoundary(typedChar) else { return }
        let before = nowNS.substring(to: nowNS.length - 1)
        let beforeNS = before as NSString
        guard let re = Self.lastWord,
              let m = re.firstMatch(in: before, range: NSRange(location: 0, length: beforeNS.length)) else { return }
        let word = beforeNS.substring(with: m.range)
        let start = m.range.location
        if speller.isReverted(word) { return }
        if start > 0 {
            let prev = beforeNS.substring(with: NSRange(location: start - 1, length: 1))
            if let part = Self.partOfWord,
               part.firstMatch(in: prev, range: NSRange(location: 0, length: (prev as NSString).length)) != nil {
                return
            }
        }
        let first = SpellWords.startsSentence(beforeNS.substring(to: start))
        let speller = self.speller
        Task { @MainActor [weak self] in
            guard let fix = await speller.ask(word, first: first), fix != word, let self else { return }
            let current = read() as NSString
            let length = (word as NSString).length
            // Only if the word is still there, untouched, where it was.
            guard current.length > start + length,
                  current.substring(with: NSRange(location: start, length: length)) == word,
                  Self.isBoundary(current.substring(with: NSRange(location: start + length, length: 1)))
            else { return }
            let fixed = current.replacingCharacters(in: NSRange(location: start, length: length), with: fix)
            self.ownChange = fixed
            self.lastFix = WordFix(start: start, from: word, to: fix)
            write(fixed)
        }
    }

    /// The backspace straight after a fix: the fix and the character after
    /// it were the end of the box, and that character went.
    private func revert(_ fix: WordFix, _ old: NSString, _ now: NSString,
                        write: (String) -> Void) -> Bool {
        let toLength = (fix.to as NSString).length
        guard old.length == fix.start + toLength + 1, now.length == old.length - 1,
              old.hasPrefix(now as String),
              old.substring(with: NSRange(location: fix.start, length: toLength)) == fix.to else { return false }
        let kept = old.substring(from: old.length - 1)
        let put = old.substring(to: fix.start) + fix.from + kept
        speller.noteReverted(fix.from)
        lastFix = nil
        ownChange = put
        write(put)
        return true
    }
}
