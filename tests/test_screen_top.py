"""§354: every screen paints its own status bar, so the strip at the top
slides and fades with the page it belongs to instead of staying put and
changing colour while the page under it is still moving."""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def test_every_screen_starts_with_its_own_status_bar_strip():
    screens = re.findall(r'<section class="screen" id="(screen-[a-z]+)">\s*(<[^>]*>)', HTML)
    assert len(screens) >= 30
    missing = [sid for sid, first in screens if 'class="screen-top"' not in first]
    assert missing == [], f"screens without a .screen-top strip first: {missing}"


def test_the_strip_sits_over_the_frames_bar():
    rule = re.search(r"\.screen-top\{([^}]*)\}", HTML).group(1)
    assert "height:var(--sb-h)" in rule
    assert "margin-top:calc(-1 * var(--sb-h))" in rule


def test_no_screen_clips_its_strip_with_overflow():
    # overflow:hidden on a screen cuts the strip pulled up above it; clip
    # with clip-path's inset that leaves the top open instead.
    for sid in ("screen-explore", "screen-player"):
        rule = re.search(r"#" + sid + r"\{([^}]*)\}", HTML).group(1)
        assert "overflow:hidden" not in rule
        assert "clip-path:inset(calc(-1 * var(--sb-h)) 0 0 0)" in rule


def test_profile_wash_reaches_the_top_opaquely():
    assert ("#screen-profile > .screen-top, #screen-person > .screen-top,\n"
            "  #screen-person .yf-bar{ background:color-mix(in srgb, var(--copper) 24%, var(--bg)); }") in HTML


def test_no_literal_body_tag_in_the_head():
    # The preview builders split the document on <body>; a comment naming
    # the tag in the stylesheet made the published preview print CSS as text.
    head = HTML.split("</head>", 1)[0]
    assert "<body" not in head
