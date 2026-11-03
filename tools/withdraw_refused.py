"""Withdraw what the archive kept from a refused provider: a list first (§430).

    python tools/withdraw_refused.py                     # the list: withdraw_refused.json/.md
    python tools/withdraw_refused.py --apply withdraw_refused.json --approve <sha256>
    python tools/withdraw_refused.py --status            # what is withdrawn, and since when
    python tools/withdraw_refused.py --restore           # undo, any time before the purge
    python tools/withdraw_refused.py --purge             # delete for good, 30 days after

API-Sports and Finnhub are refused (`history_archive.REFUSED`) and no longer
used; before §429 the archive could keep their scores and prices as live
facts while Consuela was off. Withdrawing is reversible (every-agent.md
always 8): a fact is marked inactive, which every reader already ignores, and
a reading is moved out of the series into a holding table; `--restore` puts
both back. `--purge` deletes only what was withdrawn more than
`GRACE_DAYS` ago. No model, no provider, no network beyond the archive.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Whose material is withdrawn: the refused providers no longer in use.
PROVIDERS = ("api-sports", "finnhub")
#: The mark on everything withdrawn, so restore and purge touch only this.
WHY = "withdrawn: refused provider (§430)"
#: How long withdrawn material is held before it may be deleted for good.
GRACE_DAYS = 30


def listing(store) -> dict:
    rows = store.provider_rows(PROVIDERS)
    by = {}
    for row in rows:
        key = f"{row['store']} / {row['provider']}"
        by[key] = by.get(key, 0) + 1
    return {"providers": list(PROVIDERS), "rows": rows, "counts": by}


def write_list(found: dict, path) -> str:
    path = pathlib.Path(path)
    body = dict(found, made=datetime.datetime.now(datetime.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"), why=WHY)
    data = json.dumps(body, indent=1, sort_keys=True, default=str).encode("utf-8")
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    lines = [f"# Withdraw refused providers' material - for review ({body['made']})", "",
             f"**{len(found['rows'])} records** from {', '.join(PROVIDERS)}:", ""]
    lines += [f"- {k}: {v}" for k, v in sorted(found["counts"].items())] or ["- none"]
    lines += ["", "Reversible: `--restore` puts every one back until the purge, "
              f"which waits {GRACE_DAYS} days.", "",
              f"Approve with: `python tools/withdraw_refused.py --apply {path} "
              f"--approve {digest}`", "", "| Where | Provider | What | When |", "|---|---|---|---|"]
    for r in found["rows"]:
        lines.append("| " + " | ".join(str(r[k]).replace("|", "/") for k in
                                       ("store", "provider", "title", "when")) + " |")
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return digest


def apply(store, path, approve: str, now: float | None = None) -> dict:
    data = pathlib.Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if not approve or approve.strip().lower() != digest:
        return {"withdrawn": 0, "refused": "the approval does not name this list "
                f"(its SHA-256 is {digest}); nothing was changed"}
    rows = json.loads(data)["rows"]
    done = store.withdraw(rows, WHY, now if now is not None else time.time())
    return {"withdrawn": done, "listed": len(rows)}


def purge(store, now: float | None = None) -> int:
    before = (now if now is not None else time.time()) - GRACE_DAYS * 86400
    return store.purge_withdrawn(WHY, before)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="withdraw_refused.json")
    parser.add_argument("--apply", default="")
    parser.add_argument("--approve", default="")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--purge", action="store_true")
    args = parser.parse_args(argv)

    import history_archive as ha

    store = ha.backend()
    if store is None:
        print("the archive is off or unreachable here; nothing was read")
        return 2
    if args.apply:
        out = apply(store, args.apply, args.approve)
        if out.get("refused"):
            print(out["refused"])
            return 2
        print(f"withdrew {out['withdrawn']} of {out['listed']} listed; "
              f"undo with --restore, deleted for good after {GRACE_DAYS} days with --purge")
        return 0
    if args.status:
        print(json.dumps(store.withdrawn_count(WHY), default=str))
        return 0
    if args.restore:
        print(f"restored {store.restore_withdrawn(WHY)}")
        return 0
    if args.purge:
        print(f"deleted for good {purge(store)} (withdrawn more than {GRACE_DAYS} days ago)")
        return 0
    found = listing(store)
    digest = write_list(found, args.out)
    print(f"{len(found['rows'])} records from {', '.join(PROVIDERS)}: {found['counts']}")
    print(f"the list: {args.out} (and {os.path.splitext(args.out)[0]}.md)")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
