"""Build the backend operations deck from docs/operations/slides.md.

    pip install -r requirements-docs.txt        # python-pptx
    python docs/operations/build_deck.py

slides.md format:
    # Title            starts a slide (the first slide is the title slide)
    - text             a bullet
      - text           an indented bullet (two spaces before the dash)
    > text             a speaker note
Blank lines are ignored.

Writes docs/operations/FAM_Backend_Operations.pptx. Edits made in Keynote or
PowerPoint are overwritten by the next build, so lasting changes go in
slides.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR
    from pptx.util import Emu, Inches, Pt
except ImportError:  # pragma: no cover - a message, not a stack trace
    sys.exit("python-pptx is missing. Run: pip install -r requirements-docs.txt")

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "slides.md"
OUTPUT = HERE / "FAM_Backend_Operations.pptx"

INK = RGBColor(0x14, 0x14, 0x1A)
MUTED = RGBColor(0x5A, 0x5A, 0x66)
ACCENT = RGBColor(0xE8, 0x5D, 0x2A)
PAPER = RGBColor(0xFF, 0xFF, 0xFF)
HEAD_FONT = "Helvetica Neue"
BODY_FONT = "Helvetica Neue"


def parse(text: str) -> list[dict]:
    slides: list[dict] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("# "):
            slides.append({"title": line[2:].strip(), "bullets": [], "notes": []})
            continue
        if not slides:
            raise ValueError(f"text before the first '# ' slide title: {line!r}")
        stripped = line.lstrip()
        if stripped.startswith("> "):
            slides[-1]["notes"].append(stripped[2:])
        elif stripped.startswith("- "):
            level = (len(line) - len(stripped)) // 2
            slides[-1]["bullets"].append((min(level, 2), stripped[2:]))
        else:
            # A continuation of the previous bullet.
            if slides[-1]["bullets"]:
                level, prev = slides[-1]["bullets"][-1]
                slides[-1]["bullets"][-1] = (level, prev + " " + stripped)
    return slides


def _text(frame, value: str, size: int, color: RGBColor, bold=False, font=BODY_FONT):
    frame.clear()
    p = frame.paragraphs[0]
    run = p.add_run()
    run.text = value
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = font
    run.font.color.rgb = color
    return p


def _bar(slide, left, top, width, height, color):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    return shape


def build(slides: list[dict]) -> Presentation:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    W, H = prs.slide_width, prs.slide_height
    total = len(slides)

    for index, spec in enumerate(slides):
        slide = prs.slides.add_slide(blank)
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = PAPER if index else INK

        if index == 0:
            _bar(slide, Inches(0.8), Inches(2.4), Inches(1.2), Emu(Inches(0.12)), ACCENT)
            box = slide.shapes.add_textbox(Inches(0.8), Inches(2.7), W - Inches(1.6), Inches(1.6))
            box.text_frame.word_wrap = True
            _text(box.text_frame, spec["title"], 54, PAPER, bold=True, font=HEAD_FONT)
            sub = slide.shapes.add_textbox(Inches(0.8), Inches(4.3), W - Inches(1.6), Inches(2))
            sub.text_frame.word_wrap = True
            for i, (_lvl, value) in enumerate(spec["bullets"]):
                p = sub.text_frame.paragraphs[0] if i == 0 else sub.text_frame.add_paragraph()
                run = p.add_run()
                run.text = value
                run.font.size = Pt(24 if i == 0 else 18)
                run.font.name = BODY_FONT
                run.font.color.rgb = PAPER if i == 0 else RGBColor(0xB8, 0xB8, 0xC4)
                p.space_after = Pt(8)
        else:
            _bar(slide, 0, 0, Emu(Inches(0.18)), H, ACCENT)
            title = slide.shapes.add_textbox(Inches(0.7), Inches(0.45), W - Inches(1.4), Inches(1.1))
            title.text_frame.word_wrap = True
            title.text_frame.vertical_anchor = MSO_ANCHOR.TOP
            _text(title.text_frame, spec["title"], 36, INK, bold=True, font=HEAD_FONT)

            body = slide.shapes.add_textbox(Inches(0.7), Inches(1.65), W - Inches(1.4), H - Inches(2.4))
            tf = body.text_frame
            tf.word_wrap = True
            count = len(spec["bullets"])
            size = 24 if count <= 5 else 22 if count <= 7 else 20
            for i, (level, value) in enumerate(spec["bullets"]):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.level = level
                p.space_after = Pt(10)
                marker = p.add_run()
                marker.text = ("–  " if level else "■  ")
                marker.font.size = Pt(size - 8 if not level else size - 2)
                marker.font.color.rgb = ACCENT
                marker.font.name = BODY_FONT
                run = p.add_run()
                run.text = value
                run.font.size = Pt(size - 2 * level)
                run.font.name = BODY_FONT
                run.font.color.rgb = INK if not level else MUTED

            foot = slide.shapes.add_textbox(Inches(0.7), H - Inches(0.6), W - Inches(1.4), Inches(0.4))
            _text(foot.text_frame, f"FAM backend operations  ·  {index + 1} / {total}", 11, MUTED)

        if spec["notes"]:
            slide.notes_slide.notes_text_frame.text = "\n".join(spec["notes"])
    return prs


def main() -> int:
    slides = parse(SOURCE.read_text(encoding="utf-8"))
    build(slides).save(OUTPUT)
    print(f"wrote {OUTPUT.relative_to(HERE.parent.parent)} ({len(slides)} slides)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
