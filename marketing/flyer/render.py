"""Render the printed flyer to PDF and PNG.

    python marketing/flyer/render.py

Writes flyer.pdf (US Letter, no margins) and a PNG preview, flyer.png,
beside this file. Needs network for the Google Fonts.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
CHROME = "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome"
PAGE = (HERE / "flyer.html").as_uri()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None)
        page = browser.new_page(viewport={"width": 816, "height": 1056}, device_scale_factor=2)
        # Print media for the PDF and the preview alike: on screen the sheet sits
        # on a grey desk with a margin, which would push it onto a second page.
        page.emulate_media(media="print")
        page.goto(PAGE, wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.pdf(path=str(HERE / "flyer.pdf"), width="8.5in", height="11in",
                 print_background=True, prefer_css_page_size=True)
        page.locator(".sheet").screenshot(path=str(HERE / "flyer.png"))
        print("wrote flyer.pdf and flyer.png")
        browser.close()


if __name__ == "__main__":
    main()
