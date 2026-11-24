"""The "Writing run" workflow: paid writing runs outside the sandbox (§688)."""
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Loaded by path: putting `tools/` on the import path would let its
# `trending_bank.py` shadow the app's module for every later test.
_spec = importlib.util.spec_from_file_location(
    "writing_run", ROOT / "tools" / "writing_run.py")
writing_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(writing_run)

WORKFLOW = (ROOT / ".github" / "workflows" / "writing.yml").read_text()


def test_every_offered_tool_exists():
    for tool in writing_run.TOOLS:
        assert (ROOT / tool).is_file(), tool


def test_workflow_offers_exactly_the_tools():
    block = WORKFLOW.split("options:", 1)[1].split("default:", 1)[0]
    offered = re.findall(r"^\s*-\s*(\S+)\s*$", block, re.M)
    assert tuple(offered) == writing_run.TOOLS


def test_workflow_only_runs_by_hand():
    head = WORKFLOW.split("\non:\n", 1)[1].split("jobs:", 1)[0]
    assert "workflow_dispatch:" in head
    assert "push:" not in head and "pull_request" not in head
    assert "schedule:" not in head


def test_inputs_never_reach_the_shell():
    """An input pasted into `run:` is a command; it goes through the environment."""
    steps = WORKFLOW.split("steps:", 1)[1]
    for line in steps.splitlines():
        if "inputs.args" in line or "inputs.env" in line:
            assert re.match(r"\s*RUN_(ARGS|ENV): \$\{\{ inputs\.\w+ \}\}$", line), line


def test_a_quoted_question_stays_one_argument():
    cmd = writing_run.command("write.py", '"the fed decision; rm -rf /" --minutes 3')
    assert cmd[1:] == ["write.py", "the fed decision; rm -rf /", "--minutes", "3"]


def test_a_tool_not_offered_is_refused():
    with pytest.raises(ValueError):
        writing_run.command("setup_key.py", "")
    with pytest.raises(ValueError):
        writing_run.command("", "")


def test_settings_are_name_value_pairs():
    assert writing_run.settings_from("AGENTS=1 EDITOR_AGENT=1") == {
        "AGENTS": "1", "EDITOR_AGENT": "1"}
    assert writing_run.settings_from("") == {}
    with pytest.raises(ValueError):
        writing_run.settings_from("AGENTS")


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "FAM_SECRETS", "GNEWS_KEY",
                                  "VOICE_REGISTRY_TOKEN"])
def test_a_credential_is_never_an_input(name):
    with pytest.raises(ValueError):
        writing_run.settings_from(f"{name}=abc")
