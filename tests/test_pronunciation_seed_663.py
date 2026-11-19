"""Well-known names the voice says wrong are seeded (PROBLEMS.md §663).

The owner: "We are still having pronunciation issues within the app, even
with names as similar as the country name 'Iran'". No model lists "Iran" and
the CMU scan never flags it, so the lexicon never learned it.
"""
import pronunciation
import voice_bank


def _lex(tmp_path):
    bank = voice_bank.VoiceBank(str(tmp_path / "voices.db"))
    return bank, pronunciation.Lexicon(bank)


def test_iran_is_respelled_without_any_model_listing_it(tmp_path):
    _, lex = _lex(tmp_path)
    said = lex.apply("Iran's foreign minister flew to Tehran. Iranian officials met.")
    assert "Iran" not in said.replace("Iranian", "")
    assert said.startswith("Irrahn's")
    assert "Terrahn" in said and "Irrayneeun" in said


def test_every_seed_is_a_clean_pair():
    for name, say in pronunciation.SEED.items():
        assert pronunciation.clean(name, say) or name in pronunciation._SHORT_SEED, name


def test_seed_is_shown_as_its_own_source(tmp_path):
    bank, lex = _lex(tmp_path)
    lex.entries()
    rows = {r["name"]: r["source"] for r in bank.pronunciations()}
    assert rows["Iran"] == "seed"


def test_an_admin_respelling_beats_the_seed_and_survives_a_reseed(tmp_path):
    bank, lex = _lex(tmp_path)
    lex.set("Iran", "ih-RAHN")
    again = pronunciation.Lexicon(bank)          # the next boot
    assert again.apply("Iran") == "Irrahn"


def test_a_model_guess_does_not_replace_the_seed(tmp_path):
    bank, lex = _lex(tmp_path)
    lex.entries()
    assert not bank.learn_pronunciation("Iran", "eye-RAN", "writer")
    assert not bank.learn_pronunciation("Iran", "ih-RAN", "wikipedia")
    assert lex.apply("Iran") == "Irrahn"


def test_case_matters_so_lowercase_words_are_untouched(tmp_path):
    _, lex = _lex(tmp_path)
    assert lex.apply("a red chile and the river") == "a red chile and the river"
    assert lex.apply("Super Bowl XI") == "Super Bowl XI"


# The outside audit of the list (§663).

def test_an_h_after_a_vowel_keeps_its_hyphen():
    assert pronunciation.voiced("doh-hah") == "Doh-hah"
    assert pronunciation.voiced("muh-HOHMZ") == "Muh-hohmz"
    assert pronunciation.voiced("sah-gah-poh-loo-TEH-leh") == "Sahgahpohlootelleh"
    assert pronunciation.voiced("mat-hew") == "Mat-hew"


def test_a_name_in_capitals_is_respelled_but_not_a_numeral(tmp_path):
    _, lex = _lex(tmp_path)
    assert lex.apply("IRAN AND HAMAS") == "Irrahn AND Hahmahs"
    assert lex.apply("Super Bowl XI") == "Super Bowl XI"


def test_a_dropped_seed_leaves_the_table_but_an_admins_row_stays(tmp_path):
    bank, lex = _lex(tmp_path)
    bank.learn_pronunciation("Oldname", "OLD-naym", "seed")
    bank.learn_pronunciation("Kept", "KEPT-ed", "admin")
    lex.entries()
    names = {r["name"] for r in bank.pronunciations()}
    assert "Oldname" not in names and "Kept" in names


def test_an_admin_can_replace_xi(tmp_path):
    bank, lex = _lex(tmp_path)
    assert lex.set("Xi", "SHEE") == ("Xi", "SHEE")
    assert {r["name"]: r["source"] for r in bank.pronunciations()}["Xi"] == "admin"


def test_accented_spellings_and_hyphenated_names_match(tmp_path):
    _, lex = _lex(tmp_path)
    assert lex.apply("Erdoğan met al-Qaeda's") == "Airdohwahn met al-Kyduh's"


def test_a_short_vowel_before_a_consonant_doubles_it_and_loses_its_h():
    """The owner's /admin check (§664): "ih-RAHN" handed over as "Ihrahn"
    was heard as "eef-rahn" - the h breathed before the r."""
    v = pronunciation.voiced
    assert v("ih-RAHN") == "Irrahn"
    assert v("teh-RAHN") == "Terrahn"
    assert v("zeh-LEN-skee") == "Zellenskee"
    assert v("muh-HOHMZ") == "Muh-hohmz"          # an h after a vowel keeps its hyphen
    assert v("at-ih-KOH-kan") == "Atikohkan"      # k is not doubled, the h still goes
    assert v("ih-SHAH") == "Ishah"                # nor a digraph


def test_an_unstressed_uh_is_written_a_as_english_writes_it():
    v = pronunciation.voiced
    assert v("ab-dul-kuh-REEM") == "Abdulkareem"
    assert v("uh-DAH-nee") == "Adahnee"           # not "Uddahnee", stress pulled forward
    assert v("uh-DAP-tuh-vist") == "Adaptavist"
    assert v("MUH-ham-mad") == "Muh-hammad"       # a stressed one is left


def test_a_marked_vowel_before_a_vowel_loses_its_h():
    v = pronunciation.voiced
    assert v("ah-jah-EH-roh") == "Ahja-erroh"     # was "Ahjahehroh", an h said
    assert v("BOH-en") == "Bo-en"
    assert "h" not in v("ih-RAHN")[:3]


def test_no_seed_hands_the_voice_a_breathed_h():
    """§664: a vowel-marking h inside a word is breathed or said."""
    import re
    for name, say in pronunciation.SEED.items():
        v = pronunciation.voiced(say).lower()
        assert not re.search(r"(?<=[a-z])(?:ih|eh|uh)(?=[b-df-gj-np-tv-z])|[aeiou]h(?=[aeiou])", v), (name, v)
