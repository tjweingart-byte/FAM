"""Numbers and abbreviations the way a person says them, just before the voice.

The 27/09 packet: the voice read "612 yards" as "six, one, two yards", a
10:00 kickoff as "one, zero, zero, zero", a 62-41 record as "six, two, four,
one" and a 37-14 final as "three, seven, one, four" - and abbreviations were
pronounced as though they were words. The prompt already asks for numbers "as
a person says them"; the model mostly obeys and sometimes does not, and a
rule a model mostly obeys is not a rule. Chatterbox has no text normaliser of
its own, so the digits it is handed are the digits it reads.

So the fix lives in code rather than in Claude, and it is deterministic:
`speakable` rewrites the text **handed to the voice** and nothing else. The
script, the cache, the captions and the title keep their digits - "37-14"
reads better than "thirty-seven to fourteen" on a screen - and the audio cache
is keyed on the script and the voice, so this changes no key.

What it knows, in the order it is applied (each a pattern with its own
reading, because "37-14" is a score, "62-41" before "record" is a record, and
"10:00" is a time, and one generic digit rule would read them all the same):

* dotted initials and all-capital initialisms, spelled as letters - "U.S.",
  "NFL", "CEO" - unless the capitals are a word people say ("NASA", "NATO");
* money, percentages, ordinals, decades, times of day, scores and records;
* years, read as years ("nineteen ninety-eight", "twenty twenty-six") unless
  a counted noun follows ("2000 people");
* every other number, as a cardinal - "six hundred twelve".

It never raises: an unexpected shape is left as it was, which is exactly
what the voice was given before this existed.
"""
from __future__ import annotations

import re

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]
_SCALES = [(10**12, "trillion"), (10**9, "billion"), (10**6, "million"),
           (1000, "thousand")]


def cardinal(n: int) -> str:
    """612 -> "six hundred twelve". American, so no "and"."""
    if n < 0:
        return "minus " + cardinal(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + ("-" + _ONES[ones] if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return _ONES[hundreds] + " hundred" + (" " + cardinal(rest) if rest else "")
    for size, name in _SCALES:
        if n >= size:
            big, rest = divmod(n, size)
            return cardinal(big) + " " + name + (" " + cardinal(rest) if rest else "")
    return str(n)  # pragma: no cover - every int is below some scale


_ORDINAL_WORDS = {"one": "first", "two": "second", "three": "third",
                  "five": "fifth", "eight": "eighth", "nine": "ninth",
                  "twelve": "twelfth"}


def ordinal(n: int) -> str:
    """23 -> "twenty-third"."""
    words = cardinal(n)
    head, sep, last = words.rpartition("-") if "-" in words.split(" ")[-1] else words.rpartition(" ")
    if last in _ORDINAL_WORDS:
        last = _ORDINAL_WORDS[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last = last + "th"
    return head + sep + last


def year(n: int) -> str:
    """1998 -> "nineteen ninety-eight", 2005 -> "two thousand five",
    2026 -> "twenty twenty-six", 1900 -> "nineteen hundred"."""
    if 2000 <= n <= 2009:
        return cardinal(n)
    high, low = divmod(n, 100)
    if low == 0:
        return cardinal(high) + " hundred"
    if low < 10:
        return cardinal(high) + " oh " + cardinal(low)
    return cardinal(high) + " " + cardinal(low)


def decimal(text: str) -> str:
    """"3.5" -> "three point five", "1,234" -> "one thousand two hundred thirty-four"."""
    text = text.replace(",", "")
    whole, _, frac = text.partition(".")
    out = cardinal(int(whole or "0"))
    if frac:
        out += " point " + " ".join(_ONES[int(d)] for d in frac)
    return out


# ---------------------------------------------------------------------------
# Abbreviations
# ---------------------------------------------------------------------------
#: Capitals people say as a word rather than as letters, whatever the
#: dictionary says. Since §567 most are found in the CMU dictionary instead
#: (`_said_as_word`); this list is for the ones it lacks or gets wrong, and
#: for the short ones (two or three letters) it is never asked about.
SAID_AS_WORDS = frozenset("""
NASA NATO FIFA UEFA OPEC COVID UNESCO UNICEF NAFTA SWAT NASDAQ LASIK SCOTUS
AIDS ASAP PIN RAM ROM LAN WAN GIF JPEG IKEA LEGO SARS MERS NIMBY FOMO YOLO
OK NOAA DARPA ASEAN CERN INTERPOL NORAD ICE FEMA OSHA MAGA LOL
LIV EBITDA CAPTCHA SPAC DACA FAFSA HIPAA UTEP FAANG CONCACAF CONMEBOL
NASCAR DOGE FOX ARM PAC ZIP AXIOS
""".split())

#: Capitals with a spoken form of their own, part word and part letters, or
#: a word that reads wrongly in ordinary case ("Ok").
SAID_AS = {"OK": "okay", "OPENAI": "Open A I", "USAID": "U S Aid",
           "CSPAN": "C Span"}

#: Names the voice stumbles on however they are cased, spelled as they sound
#: (§569). "Nvidia" came out choppy after §567 handed it over in ordinary
#: case: no English word opens "Nv". News style writes "Nvidia", so the
#: capitals rule alone never reached most of them.
SPELLED_AS_SOUNDED = {"nvidia": "Envidia"}
_SOUNDED = re.compile(r"\b(" + "|".join(SPELLED_AS_SOUNDED) + r")\b", re.I)

#: Capitals spelled out although the dictionary has them as an ordinary word:
#: initialisms that collide with one ("WHO" the agency, "SAT" the test).
#: Only words of four letters or more are asked of the dictionary, so a
#: two- or three-letter one needs no entry here.
SAID_AS_LETTERS = frozenset("""
AARP ACLU WNBA NCAA NHRA UNLV USMNT USWNT NYSE NVDA AAPL TSLA GOOGL MSFT AMZN
""".split())

#: How a capitalised word said as a word is handed to the voice: "title"
#: ("Nascar") or as written ("upper", "NASCAR"). Title since §567, at the
#: owner's direction to make the changes known to be right: a voice learned
#: from ordinary text, where capitals mean letters or shouting, so "NASCAR"
#: is the form most likely to come out choppy. Captions keep the capitals.
WORD_CASE = "title"

#: Each letter's name in ARPAbet without stress, the way the CMU dictionary
#: writes an initialism ("ESPN" = IY EH S P IY EH N). An entry that is
#: exactly its letters' names is said as letters.
_LETTER_PHONES = {
    "A": ("EY",), "B": ("B IY",), "C": ("S IY",), "D": ("D IY",), "E": ("IY",),
    "F": ("EH F",), "G": ("JH IY",), "H": ("EY CH",), "I": ("AY",),
    "J": ("JH EY",), "K": ("K EY",), "L": ("EH L",), "M": ("EH M",),
    "N": ("EH N",), "O": ("OW",), "P": ("P IY",), "Q": ("K Y UW",),
    "R": ("AA R",), "S": ("EH S",), "T": ("T IY",), "U": ("Y UW",),
    "V": ("V IY",), "W": ("D AH B AH L Y UW", "D AH B AH Y UW"),
    "X": ("EH K S",), "Y": ("W AY",), "Z": ("Z IY", "Z EH D"),
}

#: Short words that appear in capitals in ordinary sentences and must not be
#: spelled ("I", "A" at the start of one).
_NOT_ABBREVIATIONS = frozenset({"I", "A"})

_SPOKEN_ABBREVIATIONS = [
    (re.compile(r"\bvs\.?(?=\s)", re.I), "versus"),
    (re.compile(r"\be\.g\.", re.I), "for example"),
    (re.compile(r"\bi\.e\.", re.I), "that is"),
    (re.compile(r"\betc\.", re.I), "et cetera"),
    (re.compile(r"\bDr\.(?=\s+[A-Z])"), "Doctor"),
    (re.compile(r"\bMr\.(?=\s)"), "Mister"),
    (re.compile(r"\bMrs\.(?=\s)"), "Missus"),
    (re.compile(r"\bNo\.\s*(?=\d)"), "number "),
    (re.compile(r"\bapprox\.", re.I), "approximately"),
]

_DOTTED = re.compile(r"\b((?:[A-Za-z]\.){2,})")
#: "AT&T", "S&P", "P&G": letters either side of an ampersand.
_AMPERSAND = re.compile(r"\b([A-Z]{1,3})&([A-Z]{1,3})\b")
#: A Roman numeral after a capitalised name: "World War II", "Super Bowl LX",
#: "Henry VIII". Only I, V, X and L, and two or more of them, so "I" and
#: initialisms made of C, D and M ("DC", "MD") are never read as numbers.
_ROMAN = re.compile(r"\b([A-Z][a-z]+)\s+([IVXL]{2,6})\b(?!['’]?[a-z])")
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50}
#: Names after which a numeral is counted ("World War Two", "Super Bowl
#: Sixty"); after any other name it is a regnal number ("Henry the Eighth").
_ROMAN_COUNTED = frozenset("""War Bowl Part Chapter Book Round Phase Title Act
Volume Episode Season Stage Level Grade Class Category Type Apollo Rocky Mark
Gemini Vatican Olympiad Wrestlemania WrestleMania Rule Article Section Tier""".split())
_CAPS = re.compile(r"\b([A-Z]{2,12})(s|'s|’s)?\b")


def _spell(letters: str) -> str:
    return " ".join(letters.upper())


def _dotted(m: re.Match) -> str:
    letters = m.group(1).replace(".", "")
    return _spell(letters)


def roman_value(numeral: str) -> int:
    """"XIV" -> 14, or 0 when it is not a well-formed numeral."""
    total, prev = 0, 0
    for ch in reversed(numeral):
        v = _ROMAN_VALUES[ch]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total if total and _to_roman(total) == numeral else 0


def _to_roman(n: int) -> str:
    out = ""
    for v, sym in ((50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"),
                   (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + sym, n - v
    return out


def _roman(m: re.Match) -> str:
    name, numeral = m.group(1), m.group(2)
    n = roman_value(numeral)
    if not n:
        return m.group(0)
    if name in _ROMAN_COUNTED:
        return f"{name} {cardinal(n).title()}"
    return f"{name} the {ordinal(n).title()}"


def _ampersand(m: re.Match) -> str:
    return _spell(m.group(1)) + " and " + _spell(m.group(2))


def _spelled_as_letters(word: str, phones: str) -> bool:
    """Whether a dictionary entry is only the word's letters, named."""
    said = re.sub(r"\d", "", phones).split()
    i = 0
    for letter in word:
        for option in _LETTER_PHONES.get(letter, ()):
            part = option.split()
            if said[i:i + len(part)] == part:
                i += len(part)
                break
        else:
            return False
    return i == len(said)


def _said_as_word(word: str) -> bool:
    """Whether a word in capitals is said as a word ("NASCAR") rather than
    letter by letter ("ESPN") - §567.

    Up to §567 only `SAID_AS_WORDS` was a word and everything else of two to
    six capitals was spelled, so NASCAR and NVIDIA reached the voice as
    "N A S C A R" and "N V I D I A". Now, from four letters, the CMU
    dictionary decides: an entry that is the letters' names is spelled, any
    other entry is a word. Not in it: spelled up to six letters (UCLA,
    MSNBC), a word from seven (as the voice always had them). Two or three
    letters are spelled unless listed - the dictionary would make "AI",
    "WHO" and "SEC" into "eye", "who" and "sec".
    """
    if word in SAID_AS_LETTERS:
        return False
    if word in SAID_AS_WORDS:
        return True
    if len(word) < 4:
        return False
    import hard_words

    found = hard_words.peek()
    phones = found.get(word.lower()) if found else None
    if phones:
        return not _spelled_as_letters(word, phones)
    # Unknown, or the dictionary not loaded yet: what the voice did before.
    return len(word) > 6


def _caps(m: re.Match) -> str:
    word, tail = m.group(1), m.group(2) or ""
    if word in _NOT_ABBREVIATIONS:
        return m.group(0)
    if word in SAID_AS:
        return SAID_AS[word] + tail
    if _said_as_word(word):
        return (word.capitalize() if WORD_CASE == "title" else word) + tail
    return _spell(word) + tail


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
_NUM = r"\d{1,3}(?:,\d{3})+|\d+"
_DASH = r"\s*[-–—]\s*"
_SCALE_WORDS = r"(?:thousand|million|billion|trillion)"

_MONEY = re.compile(r"\$\s?(" + _NUM + r")(?:\.(\d{1,2}))?(?:\s?(" + _SCALE_WORDS + r"|[kKmMbB]n?)\b)?")
_PERCENT = re.compile(r"(" + _NUM + r")(?:\.(\d+))?\s?(?:%|percent\b)")
_ORDINAL = re.compile(r"\b(\d+)(st|nd|rd|th)\b", re.I)
_DECADE = re.compile(r"(?:\b(1[1-9]|20)(\d)0s\b|['’](\d)0s\b)")
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})(?!\d)(?:\s?([ap])\.?\s?m\.?(?=\W|$))?", re.I)
_HOUR_AMPM = re.compile(r"\b(\d{1,2})\s?([ap])\.?\s?m\b\.?", re.I)
#: A record, not a score: only when the words after it say so. "Season",
#: "run" and "start" were here and read "beat Arsenal 3-1 this season" and
#: "a 12-5 run" as records; they only count as "on the season".
_RECORD = re.compile(r"\b(\d{1,3})" + _DASH + r"(\d{1,3})(?:" + _DASH + r"(\d{1,3}))?(?=\s+(?:\w+\s+){0,2}?(?:record|mark|in (?:the )?(?:division|conference|league)|on the (?:season|year)))", re.I)
#: Three numbers in a row that are not a record: "a 1-2-3 inning".
_TRIPLE = re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{1,2})\b(?![-\d])")
#: A phone number, read digit by digit in its three groups.
_PHONE = re.compile(r"(?<![\d-])\(?(\d{3})\)?[-. ](\d{3})[-.](\d{4})(?![\d-])")
#: "2019-2023", "the 2024-25 season".
_YEAR_RANGE = re.compile(r"\b(1[1-9]\d\d|20\d\d)[-–](1[1-9]\d\d|20\d\d|\d\d)\b(?![-\d])")
#: "9/11", "24/7", "1/2" - never a full date with a second slash.
_SLASH = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])")
_FRACTIONS = {(1, 2): "one half", (1, 3): "one third", (2, 3): "two thirds",
              (1, 4): "one quarter", (3, 4): "three quarters"}
#: A minus sign in front of a number, not a hyphen inside a word ("COVID-19").
_NEGATIVE = re.compile(r"(?<![\w)\]])[-−](?=\d)")
_SPECIAL = [(re.compile(r"\b401\(k\)", re.I), "four oh one K")]
#: "49ers", "76ers": the number and its ending are one word.
_NUMBER_ERS = re.compile(r"\b(\d{1,3})ers\b")
_PAIR = re.compile(r"\b(\d{1,3})" + _DASH + r"(\d{1,3})\b(?![-–—\d])")
_YEAR = re.compile(r"\b(1[1-9]\d\d|20\d\d)\b(?!\s*(?:,\d|\.\d))")
_DECIMAL = re.compile(r"(?<![\d.])(\d+\.\d+)(?![\d.])(?=([A-Za-z])?)")
_INTEGER = re.compile(r"(?<![\d.A-Za-z])(" + _NUM + r")(?!\d)(?=([A-Za-z])?)")
#: Capital letters stuck to a number: "Q3", "F1", "G7", "B12" - and a
#: code (§288) such as a gene or protein, "ZBTB44", "CD4", "IL22", which the
#: voice was handed raw and hesitated over, halfway through a first line.
#: Only letters with no vowel among them are spelled: "COP29" and "COVID19"
#: are said as words, as "NATO" is.
_LETTER_NUMBER = re.compile(
    r"\b(?:([A-Z])(\d{1,2})|([B-DF-HJ-NP-TV-Z]{2,5})(\d{1,3}))\b")
#: A full date written with slashes, American order: "9/27/2026".
_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])")
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]

#: A word after a four-digit number that makes it a count, not a year. A
#: list rather than "any word ending in s", which took "is", "was", "his" and
#: "results" and read "In 2024 his team" as "two thousand twenty-four".
_COUNTED = re.compile(r"\s+(?:people|persons|yards|feet|miles|meters|metres|"
                      r"kilometers|kilometres|acres|tons|tonnes|points|votes|"
                      r"jobs|troops|homes|houses|fans|dollars|euros|pounds|"
                      r"units|cars|workers|employees|students|cases|deaths|"
                      r"calories|followers|members|residents|copies|visitors|"
                      r"games|runs|hits|steps|words|pages|years|days|hours|"
                      r"minutes|seconds|times|of\s+(?:them|those|these))\b", re.I)
#: A word before one that makes it a year whatever follows.
_YEAR_BEFORE = re.compile(r"\b(?:in|since|from|by|until|till|of|during|before|"
                          r"after|circa|around|early|late|mid|summer|winter|"
                          r"spring|fall|autumn|January|February|March|April|May|"
                          r"June|July|August|September|October|November|"
                          r"December)\s*$", re.I)


def _money(m: re.Match) -> str:
    whole, cents, scale = m.group(1), m.group(2), (m.group(3) or "").lower()
    scale = {"k": "thousand", "m": "million", "mn": "million", "b": "billion",
             "bn": "billion"}.get(scale, scale)
    if scale:
        amount = decimal(whole + ("." + cents if cents else ""))
        return f"{amount} {scale} dollars"
    n = int(whole.replace(",", ""))
    out = cardinal(n) + (" dollar" if n == 1 else " dollars")
    if cents and int(cents):
        out += " " + cardinal(int(cents.ljust(2, "0")))
    return out


def _percent(m: re.Match) -> str:
    return decimal(m.group(1) + ("." + m.group(2) if m.group(2) else "")) + " percent"


def _ordinal(m: re.Match) -> str:
    return ordinal(int(m.group(1)))


def _decade_word(tens: int) -> str:
    return "tens" if tens == 1 else _TENS[tens].replace("y", "ies")


def _decade(m: re.Match) -> str:
    if m.group(3) is not None:
        tens = int(m.group(3))
        return _decade_word(tens) if tens >= 1 else m.group(0)
    century, tens = int(m.group(1)), int(m.group(2))
    if tens == 0:
        return "two thousands" if century == 20 else cardinal(century) + " hundreds"
    return cardinal(century) + " " + _decade_word(tens)


def _meridiem(letter: str) -> str:
    return " A M" if letter.lower() == "a" else " P M"


def _stop(m: re.Match) -> str:
    """The full stop a "p.m." swallowed, when it was also the sentence's."""
    return "." if m.group(0).endswith(".") and not m.string[m.end():].strip() else ""


def _time(m: re.Match) -> str:
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if minute > 59:
        return m.group(0)
    if hour > 24:
        # Not a clock: a duration, "a 25:00 5K".
        return cardinal(hour) + (" " + cardinal(minute) if minute else " minutes")
    if minute == 0:
        spoken = cardinal(hour) + ("" if ampm else " o'clock")
    elif minute < 10:
        spoken = cardinal(hour) + " oh " + cardinal(minute)
    else:
        spoken = cardinal(hour) + " " + cardinal(minute)
    return spoken + (_meridiem(ampm) + _stop(m) if ampm else "")


def _hour_ampm(m: re.Match) -> str:
    hour = int(m.group(1))
    if hour > 12:
        return m.group(0)
    return cardinal(hour) + _meridiem(m.group(2)) + _stop(m)


def _record(m: re.Match) -> str:
    parts = [cardinal(int(g)) for g in m.groups() if g is not None]
    if len(parts) == 3:
        return f"{parts[0]}, {parts[1]} and {parts[2]}"
    return f"{parts[0]} and {parts[1]}"


def _triple(m: re.Match) -> str:
    return ", ".join(cardinal(int(g)) for g in m.groups())


def _phone(m: re.Match) -> str:
    return ", ".join(" ".join(_ONES[int(d)] for d in g) for g in m.groups())


def _year_range(m: re.Match) -> str:
    first, second = m.group(1), m.group(2)
    tail = year(int(second)) if len(second) == 4 else cardinal(int(second))
    return year(int(first)) + " to " + tail


def _slash(m: re.Match) -> str:
    a, b = int(m.group(1)), int(m.group(2))
    return _FRACTIONS.get((a, b)) or f"{cardinal(a)} {cardinal(b)}"


def _pair(m: re.Match) -> str:
    return cardinal(int(m.group(1))) + " to " + cardinal(int(m.group(2)))


def _number_ers(m: re.Match) -> str:
    words = cardinal(int(m.group(1)))
    return words + ("rs" if words.endswith("e") else "ers")


def _letter_number(m: re.Match) -> str:
    if m.group(1):
        return m.group(1) + " " + cardinal(int(m.group(2)))
    # Letter by letter, as "ZBTB 44" already read: "Z B T B forty-four".
    return " ".join(m.group(3)) + " " + cardinal(int(m.group(4)))


def _date(m: re.Match) -> str:
    month, day, yr = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return m.group(0)
    full = int(yr) if len(yr) == 4 else 2000 + int(yr)
    return f"{_MONTHS[month - 1]} {ordinal(day)}, {year(full)}"


def _spaced(words: str, m: re.Match) -> str:
    """A number glued to a unit ("3.5mm", "1.5x") keeps a space before it."""
    return words + (" " if m.group(2) else "")


def _year(m: re.Match) -> str:
    tail = m.string[m.end():m.end() + 24]
    head = m.string[max(0, m.start() - 24):m.start()]
    if _COUNTED.match(tail) and not _YEAR_BEFORE.search(head):
        return cardinal(int(m.group(1)))
    return year(int(m.group(1)))


def _integer(m: re.Match) -> str:
    return _spaced(cardinal(int(m.group(1).replace(",", ""))), m)


#: Dashes used as punctuation (§574). Chatterbox's own clean-up
#: (`chatterbox.tts.punc_norm`, run inside every `generate`) turns "—" and
#: "–" into "-" *after* it has softened " - " into a comma, so "won—again—on
#: Sunday" reached the model as "won-again-on Sunday", three words glued into
#: one, and "won – again" as "won - again". Here they become what a reader
#: hears: a comma's pause. An unspaced en dash between words
#: ("Paris–Roubaix") is a space. Number ranges are words before this runs.
_EM_DASH = re.compile(r"\s*—+\s*")
_SPACED_DASH = re.compile(r"\s+[-–]+\s+")
_EN_DASH = re.compile(r"(?<=\w)–(?=\w)")
_COMMA_TIDY = [(re.compile(r"\s+,"), ","), (re.compile(r",(\s*,)+"), ","),
               (re.compile(r",\s*([.!?;:])"), r"\1"), (re.compile(r"^\s*,\s*"), "")]


def _dashes(text: str) -> str:
    text = _EM_DASH.sub(", ", text)
    text = _SPACED_DASH.sub(", ", text)
    text = _EN_DASH.sub(" ", text)
    text = text.replace("–", ", ")
    for pattern, put in _COMMA_TIDY:
        text = pattern.sub(put, text)
    return text


def speakable(text: str) -> str:
    """`text` with numbers and abbreviations written as they are said.

    Only ever applied to what the voice is given. Never raises; on any
    surprise the text goes to the voice as it was.
    """
    if not text:
        return text
    try:
        out = text
        for pattern, words in _SPOKEN_ABBREVIATIONS:
            out = pattern.sub(words, out)
        for pattern, words in _SPECIAL:
            out = pattern.sub(words, out)
        out = _AMPERSAND.sub(_ampersand, out)
        out = _ROMAN.sub(_roman, out)
        out = _PHONE.sub(_phone, out)
        out = _DATE.sub(_date, out)
        out = _NUMBER_ERS.sub(_number_ers, out)
        out = _LETTER_NUMBER.sub(_letter_number, out)
        out = _MONEY.sub(_money, out)
        out = _PERCENT.sub(_percent, out)
        out = _ORDINAL.sub(_ordinal, out)
        out = _DECADE.sub(_decade, out)
        out = _TIME.sub(_time, out)
        out = _HOUR_AMPM.sub(_hour_ampm, out)
        out = _YEAR_RANGE.sub(_year_range, out)
        out = _RECORD.sub(_record, out)
        out = _TRIPLE.sub(_triple, out)
        out = _PAIR.sub(_pair, out)
        out = _SLASH.sub(_slash, out)
        out = _YEAR.sub(_year, out)
        out = _NEGATIVE.sub("minus ", out)
        out = _DECIMAL.sub(lambda m: _spaced(decimal(m.group(1)), m), out)
        out = _INTEGER.sub(_integer, out)
        # Abbreviations last, so the "A M" and "P M" written above, and any
        # letters a number rule produced, are not re-read.
        out = _DOTTED.sub(_dotted, out)
        out = _SOUNDED.sub(lambda m: SPELLED_AS_SOUNDED[m.group(1).lower()], out)
        out = _CAPS.sub(_caps, out)
        out = _dashes(out)
        return out
    except Exception:  # noqa: BLE001 - the voice still gets words
        return text
