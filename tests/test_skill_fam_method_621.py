"""The project skill `.claude/skills/fam-method` (§621).

It is method for later sessions, and it cites the rules rather than
restating them - so the one way it can go wrong quietly is by pointing at
a rule, a file or a tool that is no longer there. Derived from the file,
never a hand-kept list ([storage-durability]: a guard whose subject is
enumerated by hand is decorative).
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "fam-method" / "SKILL.md"
TEXT = SKILL.read_text(encoding="utf-8")


def test_it_has_the_frontmatter_a_skill_needs():
    head = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: ([^\n]+)\n---\n", TEXT)
    assert head and head.group(1) == SKILL.parent.name
    assert 40 <= len(head.group(2)) <= 1024


def test_every_rule_it_cites_exists():
    cited = set(re.findall(r"`\[([a-z0-9-]+)\]`", TEXT))
    rules = set()
    for path in (ROOT / "docs" / "claude").glob("*.md"):
        rules |= set(re.findall(r"<!-- rule:([a-z0-9-]+) -->", path.read_text(encoding="utf-8")))
    assert cited and cited <= rules, sorted(cited - rules)


def test_every_file_it_names_exists():
    named = set(re.findall(r"`((?:tools|docs|examples|tests)/[\w./-]+)`", TEXT))
    named |= set(re.findall(r"`([\w-]+\.(?:py|md|sh|json))`", TEXT))
    assert len(named) >= 10
    assert not [n for n in sorted(named) if not (ROOT / n).exists()]


def test_it_points_at_the_rules_and_stays_short():
    assert "a rule always wins over this file" in TEXT
    assert len(TEXT.splitlines()) <= 250
