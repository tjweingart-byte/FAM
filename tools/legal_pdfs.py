#!/usr/bin/env python3
"""The terms, privacy policy and support page as PDFs (PROBLEMS.md §223).

Rendered from the same templates `/terms`, `/privacy` and `/support` serve
(`app.legal_page`), in print colours, so a PDF can never say something the
site does not. Run it again whenever a page or the contact changes:

    python tools/legal_pdfs.py --email ian@familiarize.net
    python tools/legal_pdfs.py --email ian@familiarize.net terms

Writes docs/legal/<page>.pdf. With no --email it uses SUPPORT_EMAIL.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "legal"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pages", nargs="*", help="terms, privacy, support (default: all)")
    parser.add_argument("--email", default=os.environ.get("SUPPORT_EMAIL", ""),
                        help="the published contact (default: SUPPORT_EMAIL)")
    args = parser.parse_args()

    import app as appmod
    from playwright.sync_api import sync_playwright

    pages = args.pages or list(appmod.LEGAL_PAGES)
    unknown = [p for p in pages if p not in appmod.LEGAL_PAGES]
    if unknown:
        parser.error("unknown page(s): %s" % ", ".join(unknown))
    if not args.email:
        print("legal_pdfs: no --email and no SUPPORT_EMAIL; the PDFs will name no "
              "contact, which App Store review does not accept.", file=sys.stderr)
    OUT.mkdir(parents=True, exist_ok=True)
    launch = {}
    if os.path.isfile("/opt/pw-browsers/chromium"):
        launch["executable_path"] = "/opt/pw-browsers/chromium"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch)
        page = browser.new_page()
        page.emulate_media(media="print")
        for name in pages:
            page.set_content(appmod.legal_page(name, args.email), wait_until="load",
                             timeout=20000)
            target = OUT / f"{name}.pdf"
            page.pdf(path=str(target), format="Letter", print_background=True,
                     margin={"top": "0.6in", "bottom": "0.6in",
                             "left": "0.7in", "right": "0.7in"})
            print(f"wrote {target.relative_to(ROOT)}")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
