"""Write the finance workbook (financials.py) to a file.

    python tools/financials.py                         from this machine's stores
    python tools/financials.py --out ~/Desktop/FAM.xlsx
    python tools/financials.py --remote https://fam.onrender.com
                                                       production's, with FAM_ADMIN_TOKEN

The server builds the same file on request at /api/admin/financials.xlsx, so a
copy pulled with --remote is current to the minute. Reads only; no API key.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import financials  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="", help="file to write (default: dated name here)")
    ap.add_argument("--remote", default="",
                    help="a FAM server to download from, with FAM_ADMIN_TOKEN set")
    args = ap.parse_args()
    if args.remote:
        import httpx

        token = (os.environ.get("FAM_ADMIN_TOKEN") or "").strip()
        if not token:
            print("Set FAM_ADMIN_TOKEN to the server's admin token.", file=sys.stderr)
            return 2
        res = httpx.get(args.remote.rstrip("/") + "/api/admin/financials.xlsx",
                        headers={"X-Admin-Token": token}, timeout=60)
        if res.status_code != 200:
            print(f"The server answered {res.status_code}: is the token right?",
                  file=sys.stderr)
            return 1
        data = res.content
    else:
        data = financials.build()
    out = os.path.expanduser(args.out or financials.filename())
    with open(out, "wb") as fh:
        fh.write(data)
    print(f"wrote {out} ({len(data):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
