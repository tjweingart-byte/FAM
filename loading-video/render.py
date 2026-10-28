"""Render FAM's loading screen (static/index.html, #famLoading) to a
transparent-background video: ProRes 4444 .mov and VP9 .webm with alpha.
CSS and markup are sliced out of index.html so the video is the app's own
animation, not a copy of it.

    python loading-video/render.py <out-dir>

The MP4s here (green screen + alpha matte) were made from the PNG frames
with ffmpeg; MP4/H.264 cannot carry alpha.
"""
import pathlib, re, subprocess, sys
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path(sys.argv[1])
src = (ROOT / "static/index.html").read_text()
css = src[src.index("/* -------- the loading screen -------- */"):src.index("  .demo-badge{")]
html = src[src.index('<div class="fam-loading" id="famLoading"'):src.index("<!-- ============ ENTRY")]
html = html.rsplit("</div>", 1)[0] + "</div>"  # through the overlay's own close

THEMES = {"light": "#A87520", "dark": "#E0B563"}
FPS, PERIOD = 60, 2.4  # every animation on the screen repeats every 2.4s
LOOPS = 4
SIZE = 540             # CSS px; rendered at 2x -> 1080x1080

def page(copper):
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400..700&display=swap" rel="stylesheet">
<style>
:root{{ --copper:{copper}; }}
html,body{{ margin:0; background:transparent; width:{SIZE}px; height:{SIZE}px; overflow:hidden; }}
{css}
.fam-loading{{ display:flex; background:none !important; border-radius:0; }}
.fam-loading-x, .fam-steps, .gen-text{{ display:none !important; }}
</style></head><body>{html}</body></html>"""

with sync_playwright() as p:
    b = p.chromium.launch()
    for theme, copper in THEMES.items():
        frames = OUT / f"frames-{theme}"; frames.mkdir(parents=True, exist_ok=True)
        pg = b.new_page(viewport={"width": SIZE, "height": SIZE}, device_scale_factor=2,
                        reduced_motion="no-preference")
        pg.set_content(page(copper), wait_until="networkidle")
        pg.evaluate("document.fonts.load('italic 21px \"Bricolage Grotesque\"').then(() => document.fonts.ready)")
        n = round(FPS * PERIOD)
        for i in range(n):
            # Seek every animation to one steady-state moment (past all delays).
            pg.evaluate("""t => document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; })""",
                        2 * PERIOD * 1000 + i * 1000 / FPS)
            pg.screenshot(path=str(frames / f"{i:04d}.png"), omit_background=True)
        pg.close()
        pat = str(frames / "%04d.png")
        common = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                  "-stream_loop", str(LOOPS - 1), "-i", pat]
        subprocess.run(common + ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                                 "-alpha_bits", "16", str(OUT / f"fam-loading-{theme}.mov")], check=True)
        subprocess.run(common + ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "24",
                                 "-row-mt", "1", str(OUT / f"fam-loading-{theme}.webm")], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", pat,
                        "-plays", "0", "-vf", "scale=540:-1", str(OUT / f"fam-loading-{theme}.apng")], check=True)
    b.close()
