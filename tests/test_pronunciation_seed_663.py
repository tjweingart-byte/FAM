"""Well-known names the voice says wrong are seeded (PROBLEMS.md §663).

The owner: "We are still having pronunciation issues within the app, even
with names as similar as the country name 'Iran'". No model lists "Iran" and
the CMU scan never flags it, so the lexicon never learned it.
"""
import pronunciation
import voice_bank


def _lex(tmp_path):
    """A lexicon whose seed is written now, as the background thread would."""
    bank = voice_bank.VoiceBank(str(tmp_path / "voices.db"))
    lex = pronunciation.Lexicon(bank)
    assert lex.seed()
    return bank, lex


def test_iran_is_respelled_without_any_model_listing_it(tmp_path):
    _, lex = _lex(tmp_path)
    said = lex.apply("Iran's foreign minister flew to Tehran. Iranian officials met.")
    assert "Iran" not in said.replace("Iranian", "")
    assert said.startswith("Irrahn's")
    assert "Terahn" in said and "Irrayneeyun" in said


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
    lex.set("Iran", "ee-RAHN")                   # not the seed's own reading
    again = pronunciation.Lexicon(bank)          # the next boot
    assert again.seed()
    assert again.apply("Iran") == "Eerahn"
    assert {r["name"]: r["source"] for r in bank.pronunciations()}["Iran"] == "admin"


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
    assert pronunciation.voiced("doh-hah") == "Dohah"
    assert pronunciation.voiced("muh-HOHMZ") == "Muhohmz"
    assert pronunciation.voiced("sah-gah-poh-loo-TEH-leh") == "Sahgahpohlootelleh"
    assert pronunciation.voiced("mat-hew") == "Mat-hew"


def test_a_name_in_capitals_is_respelled_but_not_a_numeral(tmp_path):
    _, lex = _lex(tmp_path)
    assert lex.apply("IRAN AND HAMAS") == "Irrahn AND Hahmahs"
    assert lex.apply("Super Bowl XI") == "Super Bowl XI"


def test_a_dropped_seed_leaves_the_table_but_an_admins_row_stays(tmp_path):
    bank = voice_bank.VoiceBank(str(tmp_path / "voices.db"))
    bank.learn_pronunciation("Oldname", "OLD-naym", "seed")
    bank.learn_pronunciation("Kept", "KEPT-ed", "admin")
    assert pronunciation.Lexicon(bank).seed()
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
    assert v("teh-RAHN") == "Terahn"     # unstressed: no doubling (§677)
    assert v("zeh-LEN-skee") == "Zelenskee"
    assert v("muh-HOHMZ") == "Muhohmz"          # an h after a vowel keeps its hyphen
    assert v("at-ih-KOH-kan") == "Atikohkan"      # k is not doubled, the h still goes
    assert v("ih-SHAH") == "Ishah"                # nor a digraph


def test_an_unstressed_uh_is_written_a_as_english_writes_it():
    v = pronunciation.voiced
    assert v("ab-dul-kuh-REEM") == "Abdulkareem"
    assert v("uh-DAH-nee") == "Adahnee"           # not "Uddahnee", stress pulled forward
    assert v("uh-DAP-tuh-vist") == "Adaptuhvist"   # after the stress: as written (§677)
    assert v("MUH-ham-mad") == "Muhammad"         # the mark goes, the h stays (§677)


def test_a_marked_vowel_before_a_vowel_loses_its_h():
    v = pronunciation.voiced
    assert v("ah-jah-EH-roh") == "Ahja-erroh"     # was "Ahjahehroh"; after an a, a hyphen
    assert v("BOH-en") == "Bowen"               # a glide, not a hyphen (§677)
    assert "h" not in v("ih-RAHN")[:3]


def test_no_seed_hands_the_voice_a_breathed_h():
    """§664: a vowel-marking h inside a word is breathed or said."""
    import re
    for name, say in pronunciation.SEED.items():
        v = pronunciation.voiced(say).lower()
        assert not re.search(r"(?:ih|eh|uh)(?=[b-df-gj-np-tv-z])", v), (name, v)



# The branch review (§664).

class _Flaky:
    """A bank that refuses every write until it is told to recover."""
    def __init__(self, bank):
        self.bank, self.down = bank, True
    def learn_pronunciation(self, *a):
        if self.down:
            raise RuntimeError("database away")
        return self.bank.learn_pronunciation(*a)
    def __getattr__(self, name):
        return getattr(self.bank, name)


def test_a_seed_that_fails_is_tried_again(tmp_path, monkeypatch):
    bank = voice_bank.VoiceBank(str(tmp_path / "voices.db"))
    flaky = _Flaky(bank)
    lex = pronunciation.Lexicon(flaky)
    assert not lex.seed()
    flaky.down = False
    assert lex.seed()
    assert lex.apply("Iran") == "Irrahn"


def test_the_first_reload_never_waits_for_the_seed(tmp_path, monkeypatch):
    """Seeding runs in a thread: the listener's first chunk does not wait."""
    started = []
    monkeypatch.setattr(pronunciation.threading, "Thread",
                        lambda target, **kw: type("T", (), {"start": lambda self: started.append(target)})())
    bank = voice_bank.VoiceBank(str(tmp_path / "voices.db"))
    lex = pronunciation.Lexicon(bank)
    assert lex.apply("Iran") == "Iran"           # not yet seeded, and not waited for
    assert len(started) == 1
    lex.reload(force=True)
    assert len(started) == 1                     # never twice at once
    started[0]()                                 # the thread runs
    assert lex.apply("Iran") == "Irrahn"


def test_capitals_take_the_better_ranked_respelling(tmp_path):
    bank = voice_bank.VoiceBank(str(tmp_path / "voices.db"))
    bank.learn_pronunciation("Mcilroy", "BAD-guess", "brief")
    bank.learn_pronunciation("McIlroy", "mak-IL-roy", "admin")
    lex = pronunciation.Lexicon(bank, seed=False)
    assert lex.apply("RORY MCILROY") == "RORY Makilroy"


def test_a_models_guess_is_not_matched_in_capitals(tmp_path):
    bank = voice_bank.VoiceBank(str(tmp_path / "voices.db"))
    bank.learn_pronunciation("Keawe", "keh-AH-veh", "writer")
    lex = pronunciation.Lexicon(bank, seed=False)
    assert lex.apply("KEAWE") == "KEAWE"


def test_entries_lists_real_names_not_their_capitals(tmp_path):
    _, lex = _lex(tmp_path)
    names = lex.entries()
    assert "Iran" in names and "IRAN" not in names
    assert lex.apply("IRAN") == "Irrahn"


def test_stress_is_read_from_capitals_only():
    v = pronunciation.voiced
    assert v("Kuh-REEM") == v("kuh-REEM") == "Kareem"
    assert v("Uh-DAH-nee") == "Adahnee"


def test_a_real_h_and_a_lone_h_are_left_alone():
    v = pronunciation.voiced
    assert v("shah-EEN") == "Shaheen"
    assert not v("h-ah").startswith("-")


def test_xi_goes_through_every_admin_path():
    assert pronunciation.clean("Xi", "shee.") == ("Xi", "shee")
    assert pronunciation.clean("Xi", "xi") is None          # respelled as itself
    assert pronunciation.clean("Ok", "oh-KAY") is None      # only the seed's short names


def test_a_short_vowels_h_inside_one_syllable_is_taken_out():
    """§676: Libertad's "lee-behr-TAHD" was heard as "luh-bay-her-tahd"."""
    v = pronunciation.voiced
    assert v("lee-behr-TAHD") == "Leebairtahd"
    assert v("boo-SHEHR") == "Booshair"
    assert v("AZ-uhr-by-JAHN") == "Azerbyjahn"
    assert v("sihr-ee-AH-nee") == "Sireeyahnee"
    assert v("GLAS-tuhn-ber-ee") == "Glastunberee"
    assert v("rohd-REE-gehs") == "Rohdreeghess"     # g stays hard, s not "lay"
    assert v("AHD-mee") == "Ahdmee"                 # ah and oh were heard right
    assert v("bah-LOHN") == "Bahlohn"


# The audit of the whole list (§677).

def test_doubling_only_where_the_syllable_is_stressed():
    v = pronunciation.voiced
    assert v("dih-TROYT") == "Ditroyt"            # was "Dittroyt", DIT stressed
    assert v("MIL-ih-band") == "Miliband"         # was "Millibband"
    assert v("BREH-mee-no") == "Bremmeeno"        # stressed: doubled
    assert v("NUH-kulz") == "Nuckulz"             # a k doubles as "ck"
    assert v("kih-GAH-lee") == "Kigahlee"         # only a word that starts "ih"
    assert v("jib-RAWL-tar") == "Jibrawltar"      # never before a cluster


def test_vowels_meeting_get_a_glide_and_letters_never_triple():
    v = pronunciation.voiced
    assert v("vee-ET-nahm") == "Veeyetnahm"       # was "Veeetnahm"
    assert v("shee-zoo-OH-kah") == "Sheezoowohkah"
    assert v("GROS-see") == "Grossee"             # was "Grosssee"
    assert v("net-un-YAH-hoo") == "Netunyahoo"    # was "Netunyah-hoo"


def test_an_initialism_is_said_letter_by_letter(tmp_path):
    bank = voice_bank.VoiceBank(str(tmp_path / "voices.db"))
    bank.learn_pronunciation("QXSC", "kyoo-ex-ess-SEE", "writer")
    lex = pronunciation.Lexicon(bank, seed=False)
    assert lex.apply("QXSC rose") == "Kyoo Ex Ess See rose"   # joined: "Kyooexesssee"


def test_a_row_stored_the_wrong_way_round_is_never_used(tmp_path):
    bank = voice_bank.VoiceBank(str(tmp_path / "voices.db"))
    bank.learn_pronunciation("troo-HEE-yoh", "Trujillo", "writer")
    lex = pronunciation.Lexicon(bank, seed=False)
    assert lex.apply("troo-HEE-yoh") == "troo-HEE-yoh"
    assert pronunciation.clean("pah-EE-tah", "Paita") is None
    assert pronunciation.clean("Jaron-Keawe", "JAIR-on keh-AH-veh")


def test_a_plain_name_keeps_its_spelling_unless_an_admin_says(tmp_path):
    assert "Detroit" in pronunciation.PLAIN and "Detroit" not in pronunciation.SEED
    bank = voice_bank.VoiceBank(str(tmp_path / "voices.db"))
    bank.learn_pronunciation("Detroit", "dih-TROYT", "brief")
    lex = pronunciation.Lexicon(bank, seed=False)
    assert lex.apply("Detroit won") == "Detroit won"
    assert not pronunciation._cleaned([("Vietnam", "vee-ET-nahm")])
    lex.set("Detroit", "dee-TROYT")               # an admin's still counts
    assert lex.apply("Detroit") == "Deetroyt"


def test_the_audit_data_loads_and_every_respelling_is_clean():
    assert len(pronunciation.PLAIN) > 150
    for name, say in pronunciation._AUDITED.items():
        assert pronunciation.clean(name, say), name
        assert name not in pronunciation.PLAIN, name
