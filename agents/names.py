"""Each agent's settings, by the names `config.py` reads.

One table, so `/api/health`, the roster and the tests read the same list
rather than three copies of it. The keys are the agents' roles, which is
what the code calls them; the names the owner gave them (Netenyahu, C3P0,
...) are internal and never shown to a listener.

`switch` is the agent's own on/off setting (the overlord has none: it is
`AGENTS` itself). `models` are its model settings, each a `vendor:model-id`
or empty for code only (§346).
"""
from __future__ import annotations

#: role -> (internal name, rule book, switch setting, model settings)
AGENTS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "overlord": ("Netenyahu", "netenyahu.md", "agents",
                 ("overlord_planner_model",)),
    "contextualizer": ("C3P0", "c3p0.md", "contextualizer",
                       ("contextualizer_model",)),
    "categorizer": ("Nando", "nando.md", "categorizer", ("categorizer_model",)),
    "retriever": ("Sonic", "sonic.md", "retriever", ("retriever_model",)),
    "editor": ("Shakespeare", "shakespeare.md", "editor_agent",
               ("editor_model", "editor_search_model")),
    "supervisor": ("Mclovin", "mclovin.md", "supervisor",
                   ("supervisor_model", "supervisor_screen_model")),
    "housekeeper": ("Consuela", "consuela.md", "housekeeper",
                    ("housekeeper_model", "housekeeper_review_model")),
    "it": ("Keith Wong", "keith.md", "it_agent", ("it_model", "it_summary_model")),
}

#: The rule book every agent also follows.
EVERY_AGENT = "every-agent.md"


def setting_names() -> list[str]:
    """Every agent setting's attribute name: the switches, then the models."""
    switches = [switch for _n, _b, switch, _m in AGENTS.values()]
    models = [m for *_rest, models in AGENTS.values() for m in models]
    return switches + models
