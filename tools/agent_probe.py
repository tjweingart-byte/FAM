"""Which model id the API says served an agent's call (§353's carry-over, §354).

    python tools/agent_probe.py              # every distinct agent model, one tiny call each
    python tools/agent_probe.py --json

`agents.models.ask` refuses an answer whose `model` field is not the exact
pinned id it asked for (`wrong_model`), so if the API reports a dated id
(`claude-haiku-5-5-20261001`) for a pinned `claude-haiku-5-5`, every agent
call falls back. Phase 0 could not see which without a key; this asks. One
call per distinct model in the agents' settings, through the same adapter
`ask` uses, about 30 tokens each - a fraction of a cent. It does not go
through `ask`, so no agent switch needs to be on and nothing is written to
`agent_calls` as any agent's; each probe is still metered there under the
agent name `probe` (review, §361), so its cost is never missing.

Refuses on a zero-spend deployment (`spend_guard`) and with no Anthropic
key. Prints, per model: asked, served, and whether `ask` would accept it.
Exit 1 when any model would come back `wrong_model`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
          "required": ["ok"], "additionalProperties": False}


def models_to_probe() -> list:
    """Every distinct `vendor:model-id` the agents' settings name."""
    from agents import models, names
    from config import settings

    found = []
    for attr in names.setting_names():
        value = str(getattr(settings, attr, "") or "")
        spec, _why = models.parse_model(value) if ":" in value else (None, "")
        if spec is not None and (spec.vendor, spec.model) not in found:
            found.append((spec.vendor, spec.model))
    return found


async def probe(vendor: str, model: str) -> dict:
    from agents import adapters

    adapter = adapters.adapter_for(vendor)
    if adapter is None:
        return {"vendor": vendor, "asked": model, "served": "", "accepted": False,
                "error": f"no adapter is built for {vendor}"}
    try:
        answer = await asyncio.wait_for(adapter.send(
            model=model, system="Answer with the JSON the schema asks for.",
            user="Say ok.", schema=SCHEMA, max_tokens=64, effort="low"), 30)
    except Exception as exc:  # noqa: BLE001 - a probe reports, never raises
        return {"vendor": vendor, "asked": model, "served": "", "accepted": False,
                "error": f"{type(exc).__name__}: {exc}"[:300]}
    _meter(vendor, model, answer)
    return {"vendor": vendor, "asked": model, "served": answer.served_model,
            "accepted": answer.served_model == model, "error": ""}


def _meter(vendor: str, model: str, answer) -> None:
    """The probe's own row in `agent_calls`, priced like any call. Never
    raises."""
    try:
        import metering

        usage = metering.Usage()
        key = model if vendor == "anthropic" else f"{vendor}:{model}"
        usage.add_model_call(key, answer.usage)
        cost = metering.price_of(usage)
        metering.MeterStore().record_agent_call(
            agent="probe", task="served model", surface="background",
            status="ok" if answer.served_model == model else "wrong_model",
            vendor=vendor, model=model, served_model=answer.served_model,
            usage={"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
            cost_usd=round(cost.claude, 8), priced=cost.priced, sent=True,
            seconds=0.0, reason="tools/agent_probe.py")
    except Exception:  # noqa: BLE001 - a probe reports, never raises
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    import credentials
    import spend_guard

    if spend_guard.enabled():
        print("This deployment spends nothing (spend_guard): no call is sent.")
        return 2
    if not credentials.active("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY here: nothing can be asked.")
        return 2
    results = [asyncio.run(probe(v, m)) for v, m in models_to_probe()]
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            verdict = ("accepted" if r["accepted"] else
                       f"ERROR {r['error']}" if r["error"] else
                       "WOULD FALL BACK as wrong_model")
            print(f"{r['vendor']}:{r['asked']:<22} served {r['served'] or '-':<32} {verdict}")
    return 0 if all(r["accepted"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
