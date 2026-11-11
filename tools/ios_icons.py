"""Write the iOS app's subject icons from the web's own (`CAT_ICONS`).

    python tools/ios_icons.py           # writes ios/FAM/FAM/SubjectIcons.swift
    python tools/ios_icons.py --check   # fails if that file is stale

The web draws a subject's icon (`catIcon(name)`, static/index.html) as inline
SVG: a 24x24 box, stroked at 1.6, round caps and joins, a few dots filled.
The iOS app draws the same shapes as SwiftUI paths, generated here so the
two can never drift apart (`tests/test_ios_app_contract.py` runs --check).
Unknown names fall back to `news`, as on the web.
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "static" / "index.html"
OUT = ROOT / "ios" / "FAM" / "FAM" / "SubjectIcons.swift"


def icons() -> dict[str, str]:
    text = WEB.read_text()
    block = re.search(r"var CAT_ICONS = \{(.*?)\n\s*\};", text, re.S)
    assert block, "CAT_ICONS not found in static/index.html"
    found = {}
    for name, markup in re.findall(r'^\s*"?([a-z-]+)"?\s*:\s*\'(.*?)\',?\s*$', block.group(1), re.M):
        found[name] = markup
    assert "news" in found and len(found) >= 40, sorted(found)
    return found


# ---------------------------------------------------------------- SVG paths

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _tokens(d: str):
    for m in re.finditer(r"[MmLlHhVvCcSsQqTtAaZz]|" + _NUM.pattern, d):
        yield m.group(0)


def _flags_and_numbers(d: str):
    """Arc flags may be written run together ("0 01 5 5"); split them."""
    out = []
    i = 0
    toks = list(re.finditer(r"[MmLlHhVvCcSsQqTtAaZz]", d))
    # Simple approach: tokenise per command segment.
    segments = re.findall(r"([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)", d)
    for cmd, args in segments:
        if cmd in "Aa":
            nums = []
            s = args
            pos = 0
            field = 0
            while pos < len(s):
                if s[pos] in " ,\t\n":
                    pos += 1
                    continue
                if field % 7 in (3, 4):
                    nums.append(float(s[pos]))
                    pos += 1
                else:
                    m = _NUM.match(s, pos)
                    nums.append(float(m.group(0)))
                    pos = m.end()
                field += 1
            out.append((cmd, nums))
        else:
            out.append((cmd, [float(x) for x in _NUM.findall(args)]))
    return out


def _arc(x1, y1, rx, ry, phi, large, sweep, x2, y2):
    """Endpoint arc to cubic Béziers (SVG implementation notes F.6)."""
    if rx == 0 or ry == 0:
        return [("L", (x2, y2))]
    phi = math.radians(phi)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    rx, ry = abs(rx), abs(ry)
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0, num / den)) if den else 0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, math.ceil(abs(dt) / (math.pi / 2)))
    step = dt / n
    out = []
    for i in range(n):
        a1, a2 = t1 + i * step, t1 + (i + 1) * step
        k = 4 / 3 * math.tan((a2 - a1) / 4)

        def pt(a):
            x, y = rx * math.cos(a), ry * math.sin(a)
            return cp * x - sp * y + cx, sp * x + cp * y + cy

        def d(a):
            x, y = -rx * math.sin(a), ry * math.cos(a)
            return cp * x - sp * y, sp * x + cp * y

        p1, p2 = pt(a1), pt(a2)
        d1, d2 = d(a1), d(a2)
        out.append(("C", (p1[0] + k * d1[0], p1[1] + k * d1[1]),
                    (p2[0] - k * d2[0], p2[1] - k * d2[1]), p2))
    return out


def path_ops(d: str):
    """SVG `d` as absolute moves, lines, cubics, quads and closes."""
    ops = []
    x = y = sx = sy = 0.0
    last_c = last_q = None
    for cmd, nums in _flags_and_numbers(d):
        rel = cmd.islower()
        c = cmd.upper()
        if c == "Z":
            ops.append(("Z",))
            x, y = sx, sy
            last_c = last_q = None
            continue
        size = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}[c]
        for i in range(0, len(nums), size):
            a = nums[i:i + size]
            if c == "M":
                nx, ny = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                if i == 0:
                    ops.append(("M", (nx, ny)))
                    sx, sy = nx, ny
                else:
                    ops.append(("L", (nx, ny)))
                x, y = nx, ny
                last_c = last_q = None
            elif c == "L":
                x, y = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                ops.append(("L", (x, y)))
                last_c = last_q = None
            elif c == "H":
                x = x + a[0] if rel else a[0]
                ops.append(("L", (x, y)))
                last_c = last_q = None
            elif c == "V":
                y = y + a[0] if rel else a[0]
                ops.append(("L", (x, y)))
                last_c = last_q = None
            elif c in "CS":
                if c == "C":
                    p1 = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                    rest = a[2:]
                else:
                    p1 = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
                    rest = a
                p2 = (x + rest[0], y + rest[1]) if rel else (rest[0], rest[1])
                p3 = (x + rest[2], y + rest[3]) if rel else (rest[2], rest[3])
                ops.append(("C", p1, p2, p3))
                last_c, last_q = p2, None
                x, y = p3
            elif c in "QT":
                if c == "Q":
                    p1 = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                    rest = a[2:]
                else:
                    p1 = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
                    rest = a
                p2 = (x + rest[0], y + rest[1]) if rel else (rest[0], rest[1])
                ops.append(("Q", p1, p2))
                last_q, last_c = p1, None
                x, y = p2
            elif c == "A":
                ex, ey = (x + a[5], y + a[6]) if rel else (a[5], a[6])
                ops.extend(_arc(x, y, a[0], a[1], a[2], int(a[3]), int(a[4]), ex, ey))
                x, y = ex, ey
                last_c = last_q = None
    return ops


# ---------------------------------------------------------------- shapes

def _attrs(tag: str) -> dict[str, str]:
    return dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', tag))


def _rotate(ops, deg, cx, cy):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)

    def r(p):
        px, py = p[0] - cx, p[1] - cy
        return (ca * px - sa * py + cx, sa * px + ca * py + cy)

    return [(op[0],) + tuple(r(p) for p in op[1:]) for op in ops]


def _ellipse_ops(cx, cy, rx, ry):
    k = 0.5522847498
    return [
        ("M", (cx + rx, cy)),
        ("C", (cx + rx, cy + k * ry), (cx + k * rx, cy + ry), (cx, cy + ry)),
        ("C", (cx - k * rx, cy + ry), (cx - rx, cy + k * ry), (cx - rx, cy)),
        ("C", (cx - rx, cy - k * ry), (cx - k * rx, cy - ry), (cx, cy - ry)),
        ("C", (cx + k * rx, cy - ry), (cx + rx, cy - k * ry), (cx + rx, cy)),
        ("Z",),
    ]


def _rect_ops(x, y, w, h, r):
    if r <= 0:
        return [("M", (x, y)), ("L", (x + w, y)), ("L", (x + w, y + h)), ("L", (x, y + h)), ("Z",)]
    r = min(r, w / 2, h / 2)
    k = 0.5522847498 * r
    return [
        ("M", (x + r, y)), ("L", (x + w - r, y)),
        ("C", (x + w - r + k, y), (x + w, y + r - k), (x + w, y + r)), ("L", (x + w, y + h - r)),
        ("C", (x + w, y + h - r + k), (x + w - r + k, y + h), (x + w - r, y + h)), ("L", (x + r, y + h)),
        ("C", (x + r - k, y + h), (x, y + h - r + k), (x, y + h - r)), ("L", (x, y + r)),
        ("C", (x, y + r - k), (x + r - k, y), (x + r, y)), ("Z",),
    ]


def shapes(markup: str):
    """(ops, filled) for every element in one icon's markup."""
    out = []
    for tag in re.findall(r"<(path|circle|ellipse|rect|line|polyline)\b([^>]*)/?>", markup):
        kind, body = tag
        a = _attrs(body)
        f = lambda k, d=0.0: float(a.get(k, d))
        if kind == "path":
            ops = path_ops(a["d"])
        elif kind == "circle":
            ops = _ellipse_ops(f("cx"), f("cy"), f("r"), f("r"))
        elif kind == "ellipse":
            ops = _ellipse_ops(f("cx"), f("cy"), f("rx"), f("ry"))
        elif kind == "rect":
            ops = _rect_ops(f("x"), f("y"), f("width"), f("height"), f("rx", a.get("ry", 0)))
        elif kind == "line":
            ops = [("M", (f("x1"), f("y1"))), ("L", (f("x2"), f("y2")))]
        else:
            pts = [float(n) for n in _NUM.findall(a["points"])]
            ops = [("M", (pts[0], pts[1]))] + [("L", (pts[i], pts[i + 1])) for i in range(2, len(pts), 2)]
        rot = re.match(r"rotate\(([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\)", a.get("transform", ""))
        if rot:
            ops = _rotate(ops, float(rot.group(1)), float(rot.group(2)), float(rot.group(3)))
        filled = a.get("fill") == "currentColor" and a.get("stroke") == "none"
        out.append((ops, filled))
    return out


# ---------------------------------------------------------------- Swift

def _n(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _pt(p) -> str:
    return f"CGPoint(x: {_n(p[0])}, y: {_n(p[1])})"


def _swift_ops(ops) -> list[str]:
    lines = []
    for op in ops:
        if op[0] == "M":
            lines.append(f"p.move(to: {_pt(op[1])})")
        elif op[0] == "L":
            lines.append(f"p.addLine(to: {_pt(op[1])})")
        elif op[0] == "C":
            lines.append(f"p.addCurve(to: {_pt(op[3])}, control1: {_pt(op[1])}, control2: {_pt(op[2])})")
        elif op[0] == "Q":
            lines.append(f"p.addQuadCurve(to: {_pt(op[2])}, control: {_pt(op[1])})")
        else:
            lines.append("p.closeSubpath()")
    return lines


def swift() -> str:
    found = icons()
    cases = []
    for name in sorted(found):
        stroke, fill = [], []
        for ops, filled in shapes(found[name]):
            (fill if filled else stroke).extend(_swift_ops(ops))
        def block(lines):
            if not lines:
                return "Path()"
            body = "\n".join("            " + l for l in lines)
            return "{\n            var p = Path()\n" + body + "\n            return p\n        }()"
        cases.append(f'        case "{name}":\n'
                     f"            return (stroke: {block(stroke)},\n"
                     f"                    fill: {block(fill)})")
    return (
        "// Generated by tools/ios_icons.py from static/index.html's CAT_ICONS.\n"
        "// Do not edit by hand: run `python tools/ios_icons.py`.\n\n"
        "import SwiftUI\n\n"
        "/// A subject's icon, as the web draws it (`catIcon`): a 24x24 box,\n"
        "/// stroked at 1.6 with round caps and joins, a few dots filled. Unknown\n"
        "/// names fall back to `news`.\n"
        "enum SubjectIcons {\n"
        f"    static let names: Set<String> = [{', '.join(repr(n).replace(chr(39), chr(34)) for n in sorted(found))}]\n\n"
        "    static func shape(_ name: String) -> (stroke: Path, fill: Path) {\n"
        "        switch names.contains(name) ? name : \"news\" {\n"
        + "\n".join(cases) +
        "\n        default:\n            return (stroke: Path(), fill: Path())\n        }\n    }\n}\n\n"
        "/// The icon at a size, in the current foreground style.\n"
        "struct SubjectIcon: View {\n"
        "    let name: String\n"
        "    var size: CGFloat = 17\n\n"
        "    var body: some View {\n"
        "        let s = SubjectIcons.shape(name)\n"
        "        ZStack {\n"
        "            Glyph(path: s.stroke, box: CGRect(x: 0, y: 0, width: 24, height: 24))\n"
        "                .stroke(style: StrokeStyle(lineWidth: 1.6 * size / 24, lineCap: .round, lineJoin: .round))\n"
        "            Glyph(path: s.fill, box: CGRect(x: 0, y: 0, width: 24, height: 24))\n"
        "        }\n"
        "        .frame(width: size, height: size)\n"
        "        .accessibilityHidden(true)\n"
        "    }\n"
        "}\n"
    )


def main() -> int:
    text = swift()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT} is stale: run python tools/ios_icons.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT} ({len(icons())} icons)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
