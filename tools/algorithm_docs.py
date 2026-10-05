"""The algorithm documents: a PDF and a slide deck that explain how a
listener's taste profile is built and how Made for you ranks on it (§202).

    python tools/algorithm_docs.py             rebuild all three files
    python tools/algorithm_docs.py --if-stale  rebuild only if the algorithm moved
    python tools/algorithm_docs.py --check     exit 1 if they are stale

Writes `docs/algorithm/FAM_Algorithm.pdf`, `FAM_Algorithm.pptx`,
`ALGORITHM.md` and `fingerprint.txt`.

**Every number in them is read from the code at build time** - the weights
from `topics.EVENT_WEIGHT`, the multipliers from their constants, the worked
example from the real `taste`, `taste_tree` and `_affinity` over the seeded
category tree. Nothing is typed twice, so the documents cannot disagree with
the ranker; they can only be out of date, and `fingerprint` is what says so.

**How they stay current.** The fingerprint hashes the constants below and the
source of every function that decides Made for you. Change either and
`tests/test_algorithm_docs.py` fails until this is re-run; `./dev.sh check`
re-runs it on its own (`--if-stale`). A change that moves `ALGO_VERSION` also
needs an entry at the top of `CHANGES`, which the documents print as their
"What changed" page - the test checks that too.

The PDF needs `reportlab` and the deck `python-pptx`
(`pip install -r requirements-docs.txt`). The fingerprint needs neither, so
the test runs anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import topics as T  # noqa: E402

OUT = ROOT / "docs" / "algorithm"
PDF = OUT / "FAM_Algorithm.pdf"
PPTX = OUT / "FAM_Algorithm.pptx"
MD = OUT / "ALGORITHM.md"
FINGERPRINT = OUT / "fingerprint.txt"

#: What changed, newest first. The first entry's version must be
#: `topics.ALGO_VERSION` - add one whenever that moves.
CHANGES: list[tuple[str, str, list[str]]] = [
    ("2026-10-05.1", "§202", [
        "Taste is a subject tree: the most specific subject an episode is "
        "about takes the whole signal, each heading above it "
        f"{T.ANCESTOR_SHARE:g}x per level (Bengals > NFL > American "
        "football > sport).",
        "Chosen interests are constant: added after normalisation at "
        f"{T.INTEREST_WEIGHT:g}, never decayed, never outvoted, never "
        "taken below that by a skip. Named subjects from the interests "
        "page count as well as the eight facets.",
        "Weights: search 2 -> 1.5, finish 2 -> 1.5, pick 1.6 -> 2.2.",
        "New signal: adding something to a mix (mix_add) is worth 2.0.",
        "Most NFL, NBA, MLB and NHL teams are seeded into the category tree "
        "under their leagues, so one fan's team is a subject from their "
        "first search. A team whose name is also weather or the news "
        "('Carolina Hurricanes') is left to grow from real sports questions.",
    ]),
    ("2026-09-23.3", "§187", [
        "Owner's weights: search 2, play 1, finish 2, skip -0.5, save 2, "
        "vibe 2.5. Made for you files subjects by field; variety cap is "
        "strict; no far minor leagues; market moves on any money taste.",
    ]),
]

#: The constants that define the ranking. Their values are part of the
#: fingerprint and every one is printed on the reference page.
CONSTANTS: list[tuple[str, str]] = [
    ("HALF_LIFE", "Seconds for a signal to lose half its weight"),
    ("INTEREST_WEIGHT", "A chosen interest, constant, after normalisation"),
    ("ANCESTOR_SHARE", "Share of a signal each heading above a subject gets, per level"),
    ("SUBTAG_WEIGHT", "How much a subtag match counts next to a facet match"),
    ("CATEGORY_DEPTH_WEIGHT", "Per-level specificity of a grown category (compounds)"),
    ("FRESHNESS_BOOST", "Live story lift: 1 + this x freshness"),
    ("LOCAL_BOOST", "Live story naming the listener's city or region"),
    ("BROAD_MATCH_PENALTY", "Live story with no sign of interest (0 = excluded)"),
    ("SEMANTIC_NEAR_COSINE", "Similarity that counts as 'been near' a subject"),
    ("SUBJECT_DEPTH", "Tree depth at which a category names a subject"),
    ("RELEVANCE_FLOOR", "Below this a tile is not a recommendation"),
    ("FATIGUE_WEIGHT", "Damping per ignored showing"),
    ("FATIGUE_GRACE", "Showings before fatigue counts"),
    ("FATIGUE_FLOOR", "Fatigue never pushes below this"),
    ("ENGAGEMENT_WEIGHT", "Strength of the global tap-rate term"),
    ("ENGAGEMENT_PRIOR", "Showings before a tile's own tap rate counts"),
    ("ENGAGEMENT_FLOOR", "Lowest engagement multiplier"),
    ("ENGAGEMENT_CEILING", "Highest engagement multiplier"),
    ("SECTION_SIZE", "Tiles on a rail"),
    ("MAX_PER_FACET", "Most tiles from one heading on Made for you"),
    ("CANDIDATE_FACTOR", "Candidates per shown tile handed to the variety pass"),
    ("READY_REACH", "How deep the rail looks for already-written episodes"),
    ("STARTUP_PRIOR_STEP", "Cold start: drop per rank of a heading's popularity"),
]

#: The functions whose source decides what Made for you shows.
FUNCTIONS = (
    "taste", "tag_shares", "lineage", "interest_shares", "_tags_for_interest",
    "taste_tree", "tag_weight", "topic_tags", "_affinity", "fatigue",
    "engagement", "rank_from_history", "_off_subject", "_is_broad_match",
    "_subject_is_familiar", "_semantically_near", "_follows_markets",
    "_far_minor_league", "_is_local", "ready_first", "diversify",
    "_fill_to_minimum", "_rail_fallback", "made_for_you_candidates",
    "startup_profile", "rank_startup", "build_feed",
)


# --------------------------------------------------------------- the facts

def constant(name: str):
    return getattr(T, name)


def fingerprint() -> str:
    """A hash of everything that decides Made for you. Pure: no category
    tree, no network, no optional dependency."""
    h = hashlib.sha256()
    h.update(T.ALGO_VERSION.encode())
    h.update(json.dumps(T.EVENT_WEIGHT, sort_keys=True).encode())
    h.update(json.dumps({n: constant(n) for n, _ in CONSTANTS},
                        sort_keys=True, default=str).encode())
    h.update(json.dumps(CHANGES).encode())
    for name in FUNCTIONS:
        h.update(inspect.getsource(getattr(T, name)).encode())
    # The generator's own layout is not the algorithm, but its words are
    # the documents', so a change here is a change to them.
    h.update(Path(__file__).read_bytes())
    return h.hexdigest()


def is_stale() -> bool:
    try:
        stored = FINGERPRINT.read_text().strip()
    except OSError:
        return True
    return stored != fingerprint() or not (PDF.exists() and PPTX.exists()
                                          and MD.exists())


SIGNALS = [
    ("vibe", "VIBE! on an episode"),
    ("pick", "A subject picked from a list (interests page, Explore's check)"),
    ("mix_add", "A subject or episode added to one of your mixes"),
    ("share", "Sending an episode to someone"),
    ("save", "Save for later"),
    ("search", "Typing (or saying) a question"),
    ("complete", "Finishing an episode (85% heard)"),
    ("play", "Starting an episode"),
    ("skip", "Skipping an episode"),
]


def worked_example() -> dict:
    """A listener who follows the Bengals, run through the real code over
    the seeded category tree in a throwaway database."""
    import categories as C

    saved = T._CATEGORIES
    tmp = tempfile.mkdtemp(prefix="fam-algo-docs-")
    try:
        store = C.CategoryStore(os.path.join(tmp, "categories.db"))
        C.apply_seed(store)
        T._CATEGORIES = store
        T.reset_topic_tags()
        day = 86400.0
        now = 1_790_000_000.0
        said = [
            ("complete", "cincinnati bengals injury report", 0.5),
            ("complete", "how the bengals rebuilt their offensive line", 2),
            ("complete", "bengals playoff chances this season", 4),
            ("search", "bengals quarterback contract", 1),
            ("search", "nfl trade deadline winners", 3),
            ("play", "how do mrna vaccines work", 6),
            ("skip", "college football playoff rankings explained", 2),
        ]
        events = [T.Event("example", kind, "", text, T.tags_for_text(text),
                          at=now - ago * day) for kind, text, ago in said]
        interests = ["culture"]
        profile = T.taste(events, now, interests)
        tiles = [
            ("Bengals injury report before Sunday",
             "cincinnati bengals injury report before sunday", ("sports",)),
            ("NFL trade deadline: who won",
             "nfl trade deadline winners and losers", ("sports",)),
            ("Inside the Oscars race", "who is leading the oscars race",
             ("culture",)),
            ("College football playoff picture",
             "college football playoff picture this week", ("sports",)),
            ("A new malaria vaccine", "how the new malaria vaccine works",
             ("health", "science")),
            ("Premier League title race", "premier league title race",
             ("sports",)),
        ]
        scored = []
        for title, query, tags in tiles:
            tile = T.Topic(f"ex-{len(scored)}", title, "", query, tags, "leaf")
            scored.append({"title": title,
                           "tags": list(T.topic_tags(tile)),
                           "affinity": round(T._affinity(tile, profile), 3)})
        scored.sort(key=lambda r: -r["affinity"])
        ordered = sorted(profile.items(), key=lambda kv: (-kv[1], kv[0]))
        return {
            "said": [{"kind": k, "text": t, "days_ago": a,
                      "weight": T.EVENT_WEIGHT[k],
                      "decayed": round(T.EVENT_WEIGHT[k] * T._decay(a * day), 3)}
                     for k, t, a in said],
            "interests": interests,
            "profile": [(tag, round(w, 3)) for tag, w in ordered],
            "tree": T.taste_tree(profile),
            "tiles": scored,
            "lineage": T.lineage("bengals"),
        }
    finally:
        T._CATEGORIES = saved
        T.reset_topic_tags()
        shutil.rmtree(tmp, ignore_errors=True)


def facts() -> dict:
    return {
        "version": T.ALGO_VERSION,
        "weights": [(k, T.EVENT_WEIGHT[k], what) for k, what in SIGNALS],
        "constants": [(n, constant(n), what) for n, what in CONSTANTS],
        "half_life_days": T.HALF_LIFE / 86400,
        "changes": CHANGES,
        "example": worked_example(),
        "fingerprint": fingerprint(),
    }


# ------------------------------------------------------------- the words
#
# One outline, three renderings. Each section is (title, lead, bullets).

def outline(f: dict) -> list[tuple[str, str, list[str]]]:
    c = {n: v for n, v, _ in f["constants"]}
    return [
        ("What Made for you is",
         "The first rail on myFAM, chosen for one listener from two "
         "inventories: today's live stories and the evergreen bank.",
         ["Built on every page load, never stored - a pure query over the "
          "listener's own event log.",
          "Trending chooses first, from the world, and Made for you never "
          "shows what Trending holds.",
          "Nothing on the page generates an episode: tiles are a title and "
          "an angle, written on tap."]),
        ("1. The taste profile",
         "Every action is logged with the subjects of what it was about. "
         "Each kind of action has a weight.",
         [f"A signal halves every {f['half_life_days']:g} days.",
          "Behaviour is scaled so the strongest subject is 1.0.",
          f"Chosen interests are then added at {c['INTEREST_WEIGHT']:g}, "
          "constant: never decayed, never outvoted, never lowered by a "
          "skip. They are the part a listener controls on purpose.",
          "Impressions (a tile being shown) are never taste."]),
        ("2. A tree, not a list",
         "Subjects live in a category tree with no depth limit: "
         "sport > American football > NFL > Bengals.",
         ["The most specific subject an episode is about takes the whole "
          "signal.",
          f"Each heading above it takes {c['ANCESTOR_SHARE']:g}x per level "
          f"({c['ANCESTOR_SHARE']:g}, {c['ANCESTOR_SHARE'] ** 2:.2f}, "
          f"{c['ANCESTOR_SHARE'] ** 3:.3f} ...).",
          "So a Bengals fan's profile knows the Bengals best, and the NFL "
          "and football as a whole too.",
          "The tree grows from what listeners search; most major-league "
          "teams are seeded so one fan's team counts from day one."]),
        ("3. Scoring a tile",
         "affinity = sum over the tile's tags of (taste x specificity), "
         "divided by the square root of the number of tags.",
         [f"Specificity: a heading 1.0, a subtag {c['SUBTAG_WEIGHT']:g}, a "
          f"category {c['CATEGORY_DEPTH_WEIGHT']:g} to the power of its "
          "depth - so a story on the specific subject beats one on its "
          "heading.",
          "Plus a meaning match (embeddings) where the model is installed.",
          f"x fatigue, x freshness (1 + {c['FRESHNESS_BOOST']:g} x push, "
          f"live only), x local {c['LOCAL_BOOST']:g}, x engagement "
          f"({c['ENGAGEMENT_FLOOR']:g}-{c['ENGAGEMENT_CEILING']:g}).",
          f"Kept only above {c['RELEVANCE_FLOOR']:g}."]),
        ("4. What is never offered",
         "Exclusions, applied before or instead of a score.",
         ["Anything the listener has already heard (a moved-on live story "
          "comes back as a 'what's new' follow-up).",
          "A live story that names nothing they follow - only the heading "
          "matches, no word they used, no near paraphrase.",
          "A minor-league game on another continent, unless they follow "
          "the team or league.",
          "Anything Trending or 'What you missed' already holds."]),
        ("5. Shaping the rail",
         "After scoring, the rail is arranged, not re-ranked.",
         [f"Already-written episodes go first, {c['READY_REACH']} deep.",
          f"At most {c['MAX_PER_FACET']} tiles per heading - strictly.",
          f"Topped up to {c['SECTION_SIZE']} from evergreen tiles by "
          "affinity - never from the live pool.",
          "A learned re-ranker may reorder what cleared the floor, only if "
          "one has been trained and beat the hand order."]),
        ("6. A brand-new listener",
         "With no actions and no interests, the profile is a prior.",
         ["Headings ranked by what everyone plays, each "
          f"{c['STARTUP_PRIOR_STEP']:g} lower than the one above.",
          "The startup questions lead, a local one first if they gave a "
          "town.",
          "One play, search or chosen interest replaces the prior for good."]),
    ]


# -------------------------------------------------------------- markdown

def build_markdown(f: dict) -> str:
    out = [f"# How Made for you chooses\n",
           f"*Generated by `tools/algorithm_docs.py` from the code - "
           f"ALGO_VERSION `{f['version']}`. Do not edit by hand.*\n"]
    for title, lead, bullets in outline(f):
        out.append(f"## {title}\n\n{lead}\n")
        out.extend(f"- {b}" for b in bullets)
        out.append("")
        if title.startswith("1."):
            out.append("| Signal | Weight | What it is |\n|---|---:|---|")
            out.extend(f"| `{k}` | {w:g} | {what} |" for k, w, what in f["weights"])
            out.append("")
    ex = f["example"]
    out.append("## Worked example: a Bengals fan\n")
    out.append("What they did (weight x decay):\n")
    out.append("| Action | Text | Days ago | Weight | After decay |\n"
               "|---|---|---:|---:|---:|")
    out.extend(f"| {s['kind']} | {s['text']} | {s['days_ago']:g} | "
               f"{s['weight']:g} | {s['decayed']:g} |" for s in ex["said"])
    out.append(f"\nChosen interest: {', '.join(ex['interests'])}.\n")
    out.append("Their profile as a tree:\n\n```")
    out.extend(_tree_lines(ex["tree"]))
    out.append("```\n")
    out.append("How six tiles score (affinity, before the multipliers):\n")
    out.append("| Tile | Tags it carries | Affinity |\n|---|---|---:|")
    out.extend(f"| {t['title']} | {', '.join(t['tags'])} | {t['affinity']:g} |"
               for t in ex["tiles"])
    out.append("\n## What changed\n")
    for version, ref, lines in f["changes"]:
        out.append(f"**{version}** ({ref})\n")
        out.extend(f"- {line}" for line in lines)
        out.append("")
    out.append("## Every constant\n\n| Name | Value | Meaning |\n|---|---:|---|")
    out.extend(f"| `{n}` | {_fmt(v)} | {what} |" for n, v, what in f["constants"])
    out.append(f"\nFingerprint `{f['fingerprint'][:16]}`.\n")
    return "\n".join(out)


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


ACRONYMS = {"nfl", "nba", "mlb", "nhl", "wnba", "ufc", "mls", "ai", "tv"}


def display(label: str) -> str:
    """A tree node's name as a reader expects it: "american football" ->
    "American Football", "nfl" -> "NFL". Labels already cased are kept."""
    if label != label.lower():
        return label
    return " ".join(w.upper() if w in ACRONYMS else w.capitalize()
                    for w in label.split())


def _tree_lines(nodes: list, depth: int = 0) -> list[str]:
    lines = []
    for n in nodes:
        lines.append(f"{'    ' * depth}{display(n['label'])}  {n['weight']:.2f}")
        lines.extend(_tree_lines(n["children"], depth + 1))
    return lines


# ------------------------------------------------------------------- pdf

INK = "#18151F"
PURPLE = "#2A2733"
CORAL = "#E2694F"
COPPER = "#B8862F"
SAGE = "#5E8270"
MUTED = "#5F5A70"
TINT = "#F3F1F7"


def build_pdf(f: dict, path: Path) -> None:
    from reportlab import rl_config
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph,
                                    SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    rl_config.invariant = 1
    base = dict(fontName="Helvetica", textColor=colors.HexColor(INK),
                alignment=TA_LEFT)
    h1 = ParagraphStyle("h1", **{**base, "fontName": "Helvetica-Bold"},
                        fontSize=26, leading=31, spaceAfter=6)
    h2 = ParagraphStyle("h2", **{**base, "fontName": "Helvetica-Bold",
                                 "textColor": colors.HexColor(CORAL)},
                        fontSize=16, leading=20, spaceBefore=16, spaceAfter=6)
    body = ParagraphStyle("body", **base, fontSize=10.5, leading=15,
                          spaceAfter=4)
    lead = ParagraphStyle("lead", **{**base,
                                     "textColor": colors.HexColor(PURPLE)},
                          fontSize=12, leading=17, spaceAfter=6)
    small = ParagraphStyle("small", **{**base,
                                       "textColor": colors.HexColor(MUTED)},
                           fontSize=8.5, leading=11)
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=14,
                            bulletIndent=2)
    mono = ParagraphStyle("mono", **{**base, "fontName": "Courier"},
                          fontSize=9.5, leading=13)

    def table(rows, widths, numeric=()):
        t = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        style = [
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(INK)),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(TINT)),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(MUTED)),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#DDD9E6")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for col in numeric:
            style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
        t.setStyle(TableStyle(style))
        return t

    def cell(text):
        return Paragraph(text, ParagraphStyle("cell", parent=body,
                                              fontSize=9, leading=12,
                                              spaceAfter=0))

    story = [
        Paragraph("How Made for you chooses", h1),
        Paragraph("A listener's taste profile and the ranking built on it, "
                  "generated from FAM's code.", lead),
        Paragraph(f"ALGO_VERSION {f['version']} &middot; fingerprint "
                  f"{f['fingerprint'][:16]} &middot; regenerate with "
                  "<font name='Courier'>python tools/algorithm_docs.py</font>",
                  small),
        Spacer(1, 10),
    ]
    for title, lead_text, bullets in outline(f):
        block = [Paragraph(title, h2), Paragraph(lead_text, body)]
        block += [Paragraph(b, bullet, bulletText="•") for b in bullets]
        story.append(KeepTogether(block))
        if title.startswith("1."):
            rows = [["Signal", "Weight", "What it is"]]
            rows += [[k, f"{w:g}", cell(what)] for k, w, what in f["weights"]]
            story += [Spacer(1, 6), table(rows, [1.1 * inch, 0.8 * inch,
                                                 4.6 * inch], numeric=(1,))]
    ex = f["example"]
    story += [Paragraph("Worked example: a Bengals fan", h2),
              Paragraph("Run through the real <font name='Courier'>taste"
                        "</font> and <font name='Courier'>_affinity</font> "
                        "over the seeded category tree.", body),
              Spacer(1, 4)]
    rows = [["Action", "Text", "Days ago", "Weight", "After decay"]]
    rows += [[s["kind"], cell(s["text"]), f"{s['days_ago']:g}",
              f"{s['weight']:g}", f"{s['decayed']:g}"] for s in ex["said"]]
    story.append(table(rows, [0.9 * inch, 3.1 * inch, 0.8 * inch,
                              0.75 * inch, 0.95 * inch], numeric=(2, 3, 4)))
    story += [Spacer(1, 8),
              Paragraph(f"Chosen interest: <b>{', '.join(ex['interests'])}"
                        "</b> - held constant on top of everything else.",
                        body),
              Paragraph("Their profile, as the tree it is:", body)]
    story += [Paragraph(line.replace(" ", "&nbsp;"), mono)
              for line in _tree_lines(ex["tree"])]
    story += [Spacer(1, 8),
              Paragraph("How six tiles score (affinity, before the "
                        "multipliers):", body)]
    rows = [["Tile", "Tags it carries", "Affinity"]]
    rows += [[cell(t["title"]), cell(", ".join(t["tags"])), f"{t['affinity']:g}"]
             for t in ex["tiles"]]
    story.append(table(rows, [2.3 * inch, 3.3 * inch, 0.9 * inch],
                       numeric=(2,)))
    story += [Paragraph("What changed", h2)]
    for version, ref, lines in f["changes"]:
        story.append(Paragraph(f"<b>{version}</b> ({ref})", body))
        story += [Paragraph(line, bullet, bulletText="•") for line in lines]
    story += [PageBreak(), Paragraph("Every constant", h2)]
    rows = [["Name", "Value", "Meaning"]]
    rows += [[n, _fmt(v), cell(what)] for n, v, what in f["constants"]]
    story.append(table(rows, [2.0 * inch, 0.8 * inch, 3.7 * inch],
                       numeric=(1,)))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor(MUTED))
        canvas.drawString(0.75 * inch, 0.5 * inch,
                          f"FAM - Made for you - {f['version']}")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch,
                               str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=letter,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.75 * inch, bottomMargin=0.8 * inch,
                            title="How Made for you chooses",
                            author="FAM", subject=f"ALGO_VERSION {f['version']}")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


# ------------------------------------------------------------------ deck

def build_deck(f: dict, path: Path) -> None:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Emu, Inches, Pt

    def rgb(hexstr):
        return RGBColor.from_string(hexstr.lstrip("#"))

    DARK, LIGHT, WHITE = "#1E1B27", "#FFFFFF", "#F4EFE4"
    HEAD, BODY = "Cambria", "Calibri"
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    W = 13.333

    def slide(dark=False):
        s = prs.slides.add_slide(blank)
        bg = s.background.fill
        bg.solid()
        bg.fore_color.rgb = rgb(DARK if dark else LIGHT)
        return s

    def text(s, x, y, w, h, runs, size=16, color=INK, bold=False,
             font=BODY, align=PP_ALIGN.LEFT, name=""):
        box = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        if name:
            box.name = name
        tf = box.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = Emu(0)
        paras = runs if isinstance(runs, list) else [runs]
        for i, p_text in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            p.space_after = Pt(8)
            r = p.add_run()
            r.text = p_text
            r.font.size, r.font.bold, r.font.name = Pt(size), bold, font
            r.font.color.rgb = rgb(color)
        return box

    def bullets(s, x, y, w, h, items, size=16, color=INK):
        box = text(s, x, y, w, h, ["•  " + i for i in items], size, color)
        return box

    def title(s, words, color=INK):
        text(s, 0.6, 0.45, W - 1.2, 0.9, words, 34, color, True, HEAD,
             name="Title")

    def card(s, x, y, w, h, fill="#F3F1F7"):
        shp = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x),
                                 Inches(y), Inches(w), Inches(h))
        shp.adjustments[0] = 0.08
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
        shp.line.fill.background()
        shp.shadow.inherit = False
        return shp

    def circle(s, x, y, d, fill, label, size=16):
        shp = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y),
                                 Inches(d), Inches(d))
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
        shp.line.fill.background()
        tf = shp.text_frame
        tf.margin_left = tf.margin_right = Emu(0)
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = label
        r.font.size, r.font.bold, r.font.name = Pt(size), True, BODY
        r.font.color.rgb = rgb(LIGHT)
        return shp

    c = {n: v for n, v, _ in f["constants"]}
    sections = outline(f)
    ex = f["example"]

    # 1. Cover
    s = slide(dark=True)
    text(s, 0.8, 2.3, 11.5, 1.4, "How Made for you chooses", 48, WHITE,
         True, HEAD)
    text(s, 0.8, 3.6, 11.5, 1.0, "A listener's taste profile, and the "
         "ranking built on it", 22, "#ABA3C4")
    text(s, 0.8, 6.4, 11.5, 0.5, f"ALGO_VERSION {f['version']}  ·  "
         "generated from the code by tools/algorithm_docs.py", 12, "#8A83A0")
    s.notes_slide.notes_text_frame.text = (
        "Every number in this deck is read from topics.py when it is built.")

    # 2. The pipeline at a glance
    s = slide()
    title(s, "Four steps from what you did to what you see")
    steps = [("1", "Taste", "Your actions and chosen interests become a "
              "weighted subject tree"),
             ("2", "Score", "Every tile is matched against that tree, "
              "specific subjects counting most"),
             ("3", "Filter", "Heard, off-subject and far minor-league "
              "tiles are removed"),
             ("4", "Shape", f"Written episodes first, max {c['MAX_PER_FACET']} "
              f"per heading, {c['SECTION_SIZE']} tiles")]
    for i, (n, head, words) in enumerate(steps):
        x = 0.6 + i * 3.1
        card(s, x, 1.9, 2.85, 3.3)
        circle(s, x + 0.3, 2.2, 0.8, CORAL, n, 22)
        text(s, x + 0.3, 3.25, 2.3, 0.6, head, 22, INK, True, HEAD)
        text(s, x + 0.3, 3.95, 2.3, 1.8, words, 15, MUTED)
    text(s, 0.6, 6.3, 12, 0.6, sections[0][1], 14, MUTED)

    # 3. The signals
    s = slide()
    title(s, "Every action carries a weight")
    weights = f["weights"]
    top = max(w for _k, w, _ in weights)
    for i, (k, w, what) in enumerate(weights):
        y = 1.6 + i * 0.58
        text(s, 0.6, y, 1.6, 0.45, k, 16, INK, True)
        width = 4.6 * abs(w) / top
        bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE,
                                 Inches(2.3 if w >= 0 else 2.3),
                                 Inches(y + 0.07), Inches(max(width, 0.05)),
                                 Inches(0.32))
        bar.fill.solid()
        bar.fill.fore_color.rgb = rgb(CORAL if w < 0 else SAGE)
        bar.line.fill.background()
        text(s, 2.4 + width, y, 0.9, 0.45, f"{w:g}", 16, INK, True)
        text(s, 7.9, y, 4.9, 0.45, what, 13, MUTED)
    text(s, 0.6, 6.85, 12, 0.4, f"Each signal halves every "
         f"{f['half_life_days']:g} days. Impressions are never taste.",
         13, MUTED)

    # 4. Interests are constant
    s = slide()
    title(s, "Your chosen interests never fade")
    card(s, 0.6, 1.8, 5.6, 4.6, "#FCEDE9")
    text(s, 1.0, 2.1, 4.8, 1.4, f"{c['INTEREST_WEIGHT']:g}", 72, CORAL,
         True, HEAD)
    text(s, 1.0, 3.6, 4.8, 2.6, "added to every chosen interest after "
         "your listening is scaled to a peak of 1.0 - on day one and in "
         "year two alike", 18, INK)
    bullets(s, 6.8, 1.9, 6.0, 4.6, [
        "Never decays and is never outvoted by listening",
        "A skip cannot take it below that floor",
        "The eight headings and named subjects from the interests page "
        "both count",
        "Choosing NFL also lifts American football and sport, through "
        "the tree",
        "The only part of the algorithm a listener steers on purpose"], 17)

    # 5. The tree
    s = slide()
    title(s, "A tree, not a list")
    chain = [display(n) if n not in T.TAG_LABELS else T.TAG_LABELS[n]
             for n in reversed(["bengals"] + ex["lineage"])]
    shares = [c["ANCESTOR_SHARE"] ** i for i in range(len(chain))][::-1]
    for i, (name, share) in enumerate(zip(chain, shares)):
        x = 0.6 + i * 1.0
        y = 1.7 + i * 1.05
        card(s, x, y, 4.4, 0.85, "#EEF3F0" if i < len(chain) - 1 else "#FCEDE9")
        text(s, x + 0.3, y + 0.17, 2.6, 0.5, name, 18, INK, True)
        text(s, x + 3.0, y + 0.17, 1.2, 0.5, f"{share:.2f}", 18,
             CORAL if i == len(chain) - 1 else SAGE, True)
    bullets(s, 7.4, 1.8, 5.4, 4.8, [
        "The most specific subject takes the whole signal",
        f"Each heading above takes {c['ANCESTOR_SHARE']:g}x per level",
        "A specific story beats a general one: a category counts "
        f"{c['CATEGORY_DEPTH_WEIGHT']:g}^depth when scored",
        "No Bengals story today? The NFL and football tiles come next",
        "Most NFL, NBA, MLB and NHL teams are seeded under their league"], 16)

    # 6. Scoring
    s = slide()
    title(s, "How one tile is scored")
    card(s, 0.6, 1.7, 12.1, 1.3, "#F3F1F7")
    text(s, 0.95, 1.95, 11.5, 0.9, "(affinity + meaning) × fatigue "
         "× freshness × local × engagement", 19, INK, True,
         "Courier New")
    factors = [
        ("Affinity", "Σ taste × specificity ÷ √tags"),
        ("Freshness", f"1 + {c['FRESHNESS_BOOST']:g} × push (live only)"),
        ("Fatigue", f"down to {c['FATIGUE_FLOOR']:g} if ignored"),
        ("Local", f"×{c['LOCAL_BOOST']:g} for your town"),
        ("Engagement", f"{c['ENGAGEMENT_FLOOR']:g}-"
                       f"{c['ENGAGEMENT_CEILING']:g}, global tap rate"),
        ("Floor", f"kept only above {c['RELEVANCE_FLOOR']:g}"),
    ]
    for i, (head, words) in enumerate(factors):
        x = 0.6 + (i % 3) * 4.1
        y = 3.4 + (i // 3) * 1.7
        card(s, x, y, 3.85, 1.45)
        text(s, x + 0.3, y + 0.2, 3.3, 0.5, head, 18, CORAL, True)
        text(s, x + 0.3, y + 0.7, 3.3, 0.7, words, 15, INK)

    # 7. Worked example
    s = slide()
    title(s, "A Bengals fan's rail, computed")
    text(s, 0.6, 1.45, 5.6, 0.5, "Their profile", 18, CORAL, True)
    lines = _tree_lines(ex["tree"])[:12]
    text(s, 0.6, 1.95, 5.6, 4.9, lines, 13, INK, font="Courier New")
    text(s, 6.6, 1.45, 6.2, 0.5, "Tiles by affinity", 18, CORAL, True)
    peak = max(t["affinity"] for t in ex["tiles"]) or 1
    for i, t in enumerate(ex["tiles"]):
        y = 2.0 + i * 0.75
        text(s, 6.6, y, 3.6, 0.6, t["title"], 13, INK)
        width = 2.0 * max(t["affinity"], 0) / peak
        bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(10.3),
                                 Inches(y + 0.08), Inches(max(width, 0.04)),
                                 Inches(0.3))
        bar.fill.solid()
        bar.fill.fore_color.rgb = rgb(SAGE)
        bar.line.fill.background()
        text(s, 10.4 + width, y, 0.8, 0.5, f"{t['affinity']:.2f}", 13, INK,
             True)
    text(s, 0.6, 6.85, 12, 0.4, "Run through the real taste() and "
         "_affinity() over the seeded tree, before the multipliers.",
         12, MUTED)

    # 8. Exclusions and shaping
    s = slide()
    title(s, "Removed first, then arranged")
    for col, (head, items) in enumerate([
            (sections[4][0][3:], sections[4][2]),
            (sections[5][0][3:], sections[5][2])]):
        x = 0.6 + col * 6.2
        card(s, x, 1.7, 5.9, 4.6)
        text(s, x + 0.35, 1.95, 5.2, 0.6, head, 20, CORAL, True, HEAD)
        bullets(s, x + 0.35, 2.65, 5.2, 4.0, items, 14)

    # 9. Cold start
    s = slide()
    title(s, sections[6][0][3:])
    text(s, 0.6, 1.6, 12, 0.7, sections[6][1], 18, MUTED)
    for i, words in enumerate(sections[6][2]):
        x = 0.6 + i * 4.1
        card(s, x, 2.6, 3.85, 2.7)
        circle(s, x + 0.3, 2.9, 0.7, SAGE, str(i + 1), 18)
        text(s, x + 0.3, 3.8, 3.3, 2.1, words, 16, INK)

    # 10. What changed
    s = slide(dark=True)
    version, ref, lines = f["changes"][0]
    title(s, f"What changed in {version}", WHITE)
    bullets(s, 0.6, 1.6, 12.1, 5.2, lines, 17, WHITE)
    text(s, 0.6, 6.85, 12, 0.4, f"{ref} in PROBLEMS.md  ·  fingerprint "
         f"{f['fingerprint'][:16]}", 12, "#8A83A0")

    prs.core_properties.title = "How Made for you chooses"
    prs.core_properties.subject = f"ALGO_VERSION {f['version']}"
    prs.core_properties.author = "FAM"
    prs.save(str(path))


# ------------------------------------------------------------------- main

def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    f = facts()
    MD.write_text(build_markdown(f))
    build_pdf(f, PDF)
    build_deck(f, PPTX)
    FINGERPRINT.write_text(f["fingerprint"] + "\n")
    print(f"wrote {PDF.relative_to(ROOT)}, {PPTX.relative_to(ROOT)}, "
          f"{MD.relative_to(ROOT)} for ALGO_VERSION {f['version']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--if-stale", action="store_true")
    args = ap.parse_args(argv)
    if args.check:
        if is_stale():
            print("The algorithm documents are stale: "
                  "python tools/algorithm_docs.py")
            return 1
        print("The algorithm documents are current.")
        return 0
    if args.if_stale and not is_stale():
        print("The algorithm documents are current.")
        return 0
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
