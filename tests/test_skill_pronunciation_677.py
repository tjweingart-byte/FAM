"""The project skill `.claude/skills/pronunciation` (§677).

It is the rulebook a later session reads before writing or editing a
pronunciation, so the one way it goes wrong quietly is by describing the
voice's text in a way the code no longer produces. Every example in its
table is run through `pronunciation.voiced` here; a rule, file or tool it
names must exist (as `test_skill_fam_method_626.py` does for that skill).
"""
from __future__ import annotations

import pathlib
import re

import pronunciation

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "pronunciation" / "SKILL.md"
TEXT = SKILL.read_text(encoding="utf-8")
EXAMPLES = re.findall(r"^\| `([^`]+)` \| `([^`]+)` \|", TEXT, re.M)


def test_it_has_the_frontmatter_a_skill_needs():
    head = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: ([^\n]+)\n---\n", TEXT)
    assert head and head.group(1) == SKILL.parent.name
    assert 40 <= len(head.group(2)) <= 1024


def test_every_example_is_what_the_voice_is_handed():
    assert len(EXAMPLES) >= 15
    wrong = [(say, said, pronunciation.voiced(say)) for say, said in EXAMPLES
             if pronunciation.voiced(say) != said]
    assert not wrong, wrong


def test_the_heard_cases_are_in_it():
    for heard in ("eef-rahn", "luh-bay-her-tahd"):
        assert heard in TEXT


def test_every_rule_it_cites_exists():
    cited = set(re.findall(r"`\[([a-z0-9-]+)\]`", TEXT))
    rules = set()
    for path in (ROOT / "docs" / "claude").glob("*.md"):
        rules |= set(re.findall(r"<!-- rule:([a-z0-9-]+) -->", path.read_text(encoding="utf-8")))
    assert cited and cited <= rules, sorted(cited - rules)


def test_every_file_it_names_exists():
    named = set(re.findall(r"`((?:tools|docs|tests|pronounce_data)/[\w./-]+)`", TEXT))
    named |= set(re.findall(r"`([\w-]+\.(?:py|md|json))`", TEXT))
    assert len(named) >= 8
    missing = [n for n in sorted(named)
               if not (ROOT / n).exists() and not list(ROOT.glob("**/" + n))]
    assert not missing, missing


def test_it_points_at_the_rules_and_stays_short():
    assert "a rule always wins over this file" in TEXT
    assert len(TEXT.splitlines()) <= 250
