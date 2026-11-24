"""Run one writing tool from the "Writing run" workflow (PROBLEMS.md §688).

    RUN_TOOL=write.py RUN_ARGS='"the fed decision" --minutes 3' python tools/writing_run.py

`.github/workflows/writing.yml` is where the paid writing runs happen now
that a cloud coding session holds no Anthropic key. The workflow hands this
its three inputs in the environment and this turns them into one command:

* `RUN_TOOL` - which script, and only one of `TOOLS`;
* `RUN_ARGS` - its arguments, split as a shell would split them but never
  given to a shell, so a quoted question stays one argument and nothing in
  it is executed;
* `RUN_ENV` - `NAME=value` settings for this run (`AGENTS=1 EDITOR_AGENT=1`).
  A credential is refused here: keys are the repository's secrets, never
  something typed into a form (`[credentials-fetched]`).

It calls no model itself and costs nothing; the tool it starts does.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys

#: The scripts the workflow offers. Its `options:` list must match
#: (`tests/test_writing_run_688.py`).
TOOLS = (
    "write.py",
    "tools/ei_eval.py",
    "tools/editor_eval.py",
    "tools/nando_eval.py",
    "tools/compare_search.py",
    "compare_models.py",
    "diagnose_api.py",
)

_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SECRET = re.compile(r"(KEY|KEYS|TOKEN|SECRET|PASSWORD|SECRETS)$")


def command(tool: str, args: str) -> list[str]:
    """The command for one run. Raises `ValueError` on a tool not offered."""
    if tool not in TOOLS:
        raise ValueError(f"{tool!r} is not one of: {', '.join(TOOLS)}")
    return [sys.executable, tool, *shlex.split(args or "")]


def settings_from(text: str) -> dict[str, str]:
    """`NAME=value` pairs for the run. Raises `ValueError` on anything else."""
    out: dict[str, str] = {}
    for pair in shlex.split(text or ""):
        name, eq, value = pair.partition("=")
        if not eq or not _NAME.match(name):
            raise ValueError(f"{pair!r} is not NAME=value")
        if _SECRET.search(name):
            raise ValueError(
                f"{name} looks like a credential: add it as a repository "
                "secret, never as an input")
        out[name] = value
    return out


def main() -> int:
    try:
        cmd = command(os.environ.get("RUN_TOOL", ""), os.environ.get("RUN_ARGS", ""))
        extra = settings_from(os.environ.get("RUN_ENV", ""))
    except ValueError as err:
        print(f"writing run: {err}", file=sys.stderr)
        return 2
    print("$ " + " ".join(f"{k}={shlex.quote(v)}" for k, v in extra.items())
          + (" " if extra else "") + "python " + shlex.join(cmd[1:]), flush=True)
    return subprocess.call(cmd, env={**os.environ, **extra})


if __name__ == "__main__":
    sys.exit(main())
