"""Render FAM's icon (`static/icon.svg`) to the files browsers and phones ask for.

The mark is the double chevron that is the A in the wordmark (10.2 packet #2).
`icon.svg` is the source; everything else here is derived from it, so a change
to the mark is a change to that file and one run of this:

    python tools/make_icons.py

Writes `static/apple-touch-icon.png` (180, the home-screen picture),
`static/icon-192.png` and `static/icon-512.png` (the web manifest), and
`static/favicon.ico` (16/32/48, for the browsers that ask for /favicon.ico
before reading a page). The PNGs are full-bleed squares: iOS and Android
round the corners themselves, and a rounded square inside their mask would
show dark corners. Needs Chromium (Playwright) and ImageMagick's `convert`.
"""
from __future__ import annotations

import glob
import pathlib
import subprocess
import tempfile

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


def _launch(pw):
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                    "/opt/pw-browsers/chromium"):
        for path in sorted(glob.glob(pattern)):
            try:
                return pw.chromium.launch(executable_path=path)
            except Exception:
                continue
    return pw.chromium.launch()


def main() -> None:
    svg = (STATIC / "icon.svg").read_text()
    square = svg.replace('rx="5.4" ', "")
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page()
        jobs = [("apple-touch-icon.png", 180, square, STATIC),
                ("icon-192.png", 192, square, STATIC),
                ("icon-512.png", 512, square, STATIC),
                ("favicon-16.png", 16, svg, tmp),
                ("favicon-32.png", 32, svg, tmp),
                ("favicon-48.png", 48, svg, tmp)]
        for name, size, src, out in jobs:
            page.set_viewport_size({"width": size, "height": size})
            page.set_content("<html><body style='margin:0;background:transparent'>"
                             + src.replace("<svg ", f"<svg width='{size}' height='{size}' ", 1)
                             + "</body></html>")
            page.screenshot(path=str(pathlib.Path(out) / name), omit_background=True,
                            clip={"x": 0, "y": 0, "width": size, "height": size})
        browser.close()
        subprocess.run(["convert"] + [str(pathlib.Path(tmp) / f"favicon-{s}.png")
                                      for s in (16, 32, 48)]
                       + [str(STATIC / "favicon.ico")], check=True)
    print("wrote apple-touch-icon.png, icon-192.png, icon-512.png, favicon.ico")


if __name__ == "__main__":
    main()
