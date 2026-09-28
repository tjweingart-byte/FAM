"""CLAUDE.md is a core of one line per rule; docs/claude/ holds the rest.

The split (PROBLEMS.md §165) is only safe while every rule in the topic files
still has its line in the core. A rule that exists only in a file nobody loads
is a rule nobody follows, and nothing would fail - so the IDs are compared in
both directions here rather than trusted.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE = ROOT / "CLAUDE.md"
TOPICS = sorted((ROOT / "docs" / "claude").glob("*.md"))

# The core is loaded into every session and every subagent. It grew to 186 KB
# one paragraph at a time; the budget is what stops that happening again.
CORE_BUDGET_BYTES = 40_000

MARKER = re.compile(r"^<!-- rule:([a-z0-9-]+) -->$", re.M)
CITED = re.compile(r"\[([a-z0-9][a-z0-9-]*)\]")


def _markers():
    found = []
    for path in TOPICS:
        found += [(m, path.name) for m in MARKER.findall(path.read_text())]
    return found


def test_there_are_topic_files():
    assert TOPICS, "docs/claude/*.md is missing"


def test_every_rule_id_is_unique():
    ids = [m for m, _ in _markers()]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"rule ids used twice in docs/claude: {dupes}"


def test_every_rule_in_the_topic_files_has_a_line_in_the_core():
    cited = set(CITED.findall(CORE.read_text()))
    missing = [(i, f) for i, f in _markers() if i not in cited]
    assert not missing, (
        "these rules are in docs/claude but have no line in CLAUDE.md, so no "
        f"session will see them: {missing}")


def test_every_id_the_core_cites_exists():
    known = {i for i, _ in _markers()}
    dangling = sorted(set(CITED.findall(CORE.read_text())) - known)
    assert not dangling, (
        f"CLAUDE.md cites ids with no <!-- rule:ID --> marker: {dangling}")


def test_the_core_names_every_topic_file():
    core = CORE.read_text()
    unnamed = [p.name for p in TOPICS if f"docs/claude/{p.name}" not in core]
    assert not unnamed, f"CLAUDE.md does not point to: {unnamed}"


def test_the_core_stays_within_budget():
    size = len(CORE.read_bytes())
    assert size <= CORE_BUDGET_BYTES, (
        f"CLAUDE.md is {size} bytes (budget {CORE_BUDGET_BYTES}). It loads into "
        "every session: put reasoning and history in docs/claude/ or "
        "PROBLEMS.md and keep one line per rule here.")


def test_problems_index_is_current():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "problems_index.py"), "--check"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
