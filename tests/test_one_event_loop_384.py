"""Tools that make several model calls keep one event loop (§384): the
adapter's client is shared, and a connection reused after `asyncio.run`
closed its loop fails as a connection error on the next call."""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_each_tool_that_calls_models_runs_one_loop():
    for name in ("tools/agent_probe.py", "tools/nando_eval.py",
                 "tools/draft_category_vocabulary.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        loops = text.count("asyncio.run(") + text.count("asyncio.new_event_loop()")
        assert loops == 1, (name, loops)
