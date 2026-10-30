"""FAM's agents (docs/FAM_AGENTS.md, PROBLEMS.md §340-§342).

An agent is a worker that uses a model for one named job. None is built
yet: this package is the framework they will run on, and with `AGENTS=0`
(the default) nothing in it runs and listeners hear exactly what they heard
before it existed.

* `names` - each agent's switch and model settings, by the names
  `config.py` reads, in one table.
* `models` - FAM's own thin model layer: one typed request through one
  adapter per vendor, a pinned model per agent, a consent check in code, a
  time limit, and a metered record of every call. Never raises.
* `adapters` - the vendor SDKs behind `models`; `anthropic` only for now.
* `rulebooks` - the lock on each agent's rule book (§342 builds it in its
  own change; until then every agent is held).
"""
from __future__ import annotations

from config import settings


def enabled() -> bool:
    """`AGENTS`: the one switch over every agent."""
    return bool(settings.agents)
