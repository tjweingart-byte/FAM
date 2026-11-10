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
        guard let answer = try? await api.spell([word], first: [first]) else { return nil }
        let fix: String? = answer.corrections.first ?? nil
        cache[k] = fix
        return fix
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
        let answer = try? await api.spell(ask.map { $0.word }, first: ask.map { $0.first })
        if let answer {
            for (i, w) in ask.enumerated() {
                let fix: String? = i < answer.corrections.count ? answer.corrections[i] : nil
                cache[Self.key(w.word, w.first)] = fix
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
