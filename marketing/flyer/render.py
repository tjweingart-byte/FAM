"""Render the printed flyer to PDF and PNG, both colourways.

    python marketing/flyer/render.py

Writes flyer-dark.pdf / flyer-light.pdf (US Letter, no margins) and a PNG
preview of each beside this file. Needs network for the Google Fonts.
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
        for name, query in (("dark", ""), ("light", "?light")):
            page.goto(PAGE + query, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.pdf(path=str(HERE / f"flyer-{name}.pdf"), width="8.5in", height="11in",
                     print_background=True, prefer_css_page_size=True)
            page.emulate_media(media="print")
            page.locator(".sheet").screenshot(path=str(HERE / f"flyer-{name}.png"))
            page.emulate_media(media="screen")
            print(f"wrote flyer-{name}.pdf and flyer-{name}.png")
        browser.close()


if __name__ == "__main__":
    main()
