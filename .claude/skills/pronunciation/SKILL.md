---
name: pronunciation
description: How FAM's voice (Chatterbox) turns a respelling into sound, and how to write, check and fix a pronunciation - the respelling format, the joining rules in pronunciation.py with heard evidence, when to leave a name plain, initialisms, sources and precedence, the /admin listening tools and the audit data. Use before adding or editing any respelling, a seed, the PLAIN list, the joining rules, or anything that changes the text handed to the voice.
---

# Pronunciation: how the voice reads, and how to respell for it

The rules are in `CLAUDE.md` and `docs/claude/`, and
a rule always wins over this file: `[op-voice]` above all, and `[nothing-before-material]`
for anything on the synthesis path. This file is how to work inside them.
Its examples are checked against the code by
`tests/test_skill_pronunciation_677.py`: if one fails, the code changed
how a word is voiced - update this file *with* the reason, never the
other way round. History: PROBLEMS.md §165, §289, §576, §663-§677.

## 1. What the voice is given

Chatterbox reads English spelling and nothing else: no phonemes, no SSML,
no stress marks, no lexicon of its own. Whatever letters it is handed are
the whole instruction. On the way to it (`pipeline.py`, every chunk):

1. `pronunciation.respell` swaps every known name for its respelling,
   passed through `voiced()`;
2. `spoken_text.speakable` then writes numbers and capitals as said.

So a respelling is never read as written in the lexicon. `ih-RAHN` is
stored; **"Irrahn"** is what the voice reads. Always judge the voiced
form: `python3 -c "import pronunciation as P; print(P.voiced('ih-RAHN'))"`.
Captions, titles and the cache keep the real spelling; only the voice's
text changes.

## 2. The respelling format

- Syllables joined by hyphens, words by spaces; the stressed syllable in
  capitals: `sah-gah-poh-loo-TEH-leh`. Letters, spaces, hyphens only
  (`pronunciation.clean`). Capitals carry stress *to the joining rules*
  below; the voice never sees them.
- A bare vowel is marked with an h: `ih` (sit), `eh` (bet), `uh`
  (but/about), `ah` (father), `oh` (go). `ee`, `oo`, `ay`, `eye`/`y`,
  `ow` (cow) as in English.
- Name the person, not the spelling: the same letters can be two people
  (Albanese the prime minister, Albanese elsewhere). Read the sentence the
  name came in (the export's `context`) before trusting a respelling.

## 3. How joining works - with what was heard

Since §576 each word's syllables are handed over as one word: hyphens made
Chatterbox chop a name into clipped pieces. Joining is where every
problem since has come from, so `_inside` and `_joined` rewrite each meeting
of two syllables the way English would spell the sound.

**Heard on /admin's voice check** (phoneme recogniser, `voice_check.py`):
"Ihrahn" was heard "eef-rahn"; "Leebehrtahd" "luh-bay-her-tahd";
"Kahmuhluh" "kah-moo-lu" (an "uh" read "oo"). "-rahn" and "-tahd" were
heard right, and the owner heard "Irrahn" right (§678) - where the owner's
ear and the recogniser disagree, the ear wins. Rows not marked heard in
section 7 are reasoned from English spelling and **unheard**.

| respelling | voice reads | rule |
|---|---|---|
| `ih-RAHN` | `Irrahn` | a word opening "ih" doubles, or bare "I" is "eye" |
| `lee-behr-TAHD` | `Leebairtahd` | an h inside a syllable goes: ehr = air |
| `AZ-uhr-by-JAHN` | `Azerbyjahn` | uhr = er, ihr = ir |
| `rohd-REE-gehs` | `Rohdreeghess` | g kept hard (gh), a last s doubled |
| `BREH-mee-no` | `Bremmeeno` | stressed short vowel: next consonant doubles |
| `NUH-kulz` | `Nuckulz` | a doubled k is ck |
| `dih-TROYT` | `Ditroyt` | unstressed: the h goes, no doubling |
| `zeh-LEN-skee` | `Zelenskee` | doubled, it read ZELL-en-skee |
| `ab-dul-kuh-REEM` | `Abdulkareem` | uh before the stress is a |
| `uh-bih-SHAYK` | `Abishayk` | uh opening a word is a (about) |
| `KAH-muh-luh` | `Kahmala` | every unstressed uh is a; heard "moo" as uh |
| `GAH-zuh` | `Gahza` | a last unstressed uh is a too ("lu" as uh) |
| `moh-HAH-med` | `Mohahmed` | before an h syllable the mark goes, not the h |
| `vee-ET-nahm` | `Veeyetnahm` | vowels meeting: y after e/i |
| `shee-zoo-OH-kah` | `Sheezoowohkah` | w after o/u |
| `ah-jah-EH-roh` | `Ahja-erroh` | after a, a hyphen (English has no glide) |
| `mat-hew` | `Mat-hew` | a hyphen where joining makes th, sh, ng... |
| `GROS-see` | `Grossee` | never three of one letter |
| `AHD-mee` | `Ahdmee` | ah and oh before a consonant are left |

## 4. Before you respell: should this name be respelled at all?

Most mistakes in the audit (§677) were respellings that did not need to
exist. A respelling replaces a reading the voice learned from millions of
sentences with one we made up. Leave a name plain when:

- it is an ordinary English word or name (Hamilton, Murphy, Titan);
- it is famous and its spelling is how English says it (Detroit, Vietnam,
  LeBron, Selena Gomez, Pakistan) - "Detroit" respelled became "Dittroyt";
- one spelling fires on several people or places said differently
  ("Costa" in Portuguese fired on every Contra Costa).

Those names are listed in `pronounce_data/audited.json` (`plain`):
`pronunciation.PLAIN`. A model's, the seed's or Wikipedia's respelling is
never used for them and a model's `<<SAY:>>` line for one is not learned;
an admin's still is. Respell a famous name only on evidence the voice gets
it wrong - Iran was respelled because the owner heard it.

## 5. Initialisms, codes and capitals

**A name the product leans on gets one structured entry, never "plain".**
NVIDIA is why the list exists: scripts write NVIDIA, Nvidia and nVidia, and
each went down a different path. A seed for the mixed-case form is matched
in capitals too, so one respelling (`en-VID-ia`, "Envidia" - heard
"perfect"; "Envideeya" was not, §680) covers every spelling; a test pins that all of them reach the voice alike. An
admin row for one spelling beats the seed for that spelling only - keep it
the same or remove it.

- **An initialism needs no entry.** `spoken_text` spells a capitals word
  as letters ("ASML" -> "A S M L", §567) and says one it knows as a word
  ("NVIDIA" -> "Envidia"). An entry respelled only in letters' names is
  ignored for that reason: "Ay Ess Em El" was heard "ah-ee-yes-mel" (§679).
  A capitals name respelled as a word is handed over a syllable a word
  only if every syllable is a letter's name.
- A mixed name with an initialism ("ChatGPT", "OpenAI") is not in capitals:
  write the letters as separate words, `chat jee pee TEE`.
- An ordinary name's capitals ("IRAN" in a headline) match its entry, from
  four letters, for admin, seed and Wikipedia rows only.

## 6. Where a respelling comes from, and what wins

Precedence (`pronunciation.SOURCES`, `voice_bank.learn_pronunciation`):
**admin > seed > wikipedia > writer > brief**. An admin's is never
replaced by anything.

- **brief** and **writer**: models listing hard names (`<<SAY: Name =
  respelling>>`). The weakest; most of the audit's findings were theirs.
- **wikipedia**: `wiki_pronounce.py`, an article's own respelling or IPA.
- **seed**: `pronunciation.SEED` in code plus `audited.json`'s `respell`,
  written to the table once per process in a background thread. Use it for
  a fix that must reach production without an admin's hand. Removing a name
  from the seed removes its seed row at the next boot.
- **admin**: /admin. Only the owner. Code never changes an admin's row: for
  one, write a proposal file (`name,say,confidence,reason`) for /admin's
  review upload (`pronounce_review.py`), which saves it as the admin's only
  when accepted, and list removals for the owner by hand.
- A row whose *name* is itself a respelling ("troo-HEE-yoh" = Trujillo)
  was stored the wrong way round; `looks_respelled` refuses and skips it.

## 7. Checking: never claim a respelling works unheard

This container has no GPU: nothing here can hear Chatterbox. Say so.

- `/admin`: each entry's "hear" plays it and shows what the recogniser
  heard ("The voice said it a little differently: eef-rahn"); **hear all**
  runs the list, 150 a run. Its verdict is a hint; the owner's ear decides.
- "Hear 4 ways" (`tools/stress_trial.py`) compares spellings of one name.
- `hard_words.py` lists words in episodes the CMU dictionary lacks.
- The owner's export (/admin download) is the whole list with a sentence
  per name; audit it by reading the voiced form of every row, as §677 did
  with four readers, and keep it out of the repository (it quotes
  episodes).

**Heard by the owner** (§678-§679), voiced form -> what they heard:

| name | voice got | heard |
|---|---|---|
| Iran | Irrahn | right |
| Kamala | Kahmuhluh, then Kahmala | "kah-moo-lu", then "a little off" |
| Libertad | Leebairtahd | right |
| Tehran | Terahn | pretty good, the te a little weird |
| Capito, Eugenie | Kapitoh, Yoozhanee | right, ok |
| Etihad, Premanand Maharaj | Etihahd, Praymanund Mahahrahj | right |
| Doué | Dooway | right |
| Raúl Torrez | Ra-ool Torez | right, but **a hyphen is a long pause** |
| Gemini, Duet, Emery | Jeminye, Doowet, Emaree | terrible, off, way off |
| Fuad | Foowahd | way off |
| Guterres | Gooterresh | bad: the sh (the respelling, not a rule) |
| NVIDIA | Envideeya | em for en, stress on the last i |
| ASML | Ay Ess Em El | way off |

Lessons: an ordinary word respelled (Gemini, Duet, Emery) is worse than
the word; "w" after "oo" before a vowel failed twice (Duet, Fuad); a
hyphen is a pause, not a join. **The /admin check plays the word alone**,
with no sentence, and a lone word is where this voice garbles most (the
"dahs-tee" before Emery): judge a bad one again in a sentence ("Hear 4
ways" says it inside `pronounce_review.CARRIER`) before changing a rule.

**Open questions for the ear** (unheard, decided by reasoning):

1. What should two vowels meeting become? "w" after "oo" failed (Duet,
   Fuad), a hyphen pauses (Raúl), a plain join risks a new vowel pair.
2. Does "a" for an unstressed "uh" ever take the stress ("Kahmala" as
   kah-MAH-la, "Akraman")?
3. Do the remaining hyphens ("Ahja-erroh", "Mat-hew") chop the word?
4. Do the glides sound natural ("Veeyetnahm", "Sheezoowohkah")?

## 8. Before you finish

- Run every respelling you add through `voiced()` and read the result
  aloud as an English reader would - not as you meant it.
- Add a heard example to the table in section 3 when the owner hears one.
- Change the rules only with a test of the words that motivated the change
  and the words it must not break; rerun the whole export through
  `voiced()` and count what changed (`tests/test_pronunciation_seed_663.py`
  holds the cases so far).
- Log it in PROBLEMS.md (`[problems-log]`) and ship as `[ship-loop]` says.
