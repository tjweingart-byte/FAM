"""Parse every inline <script> in the interface, and refuse a name declared twice.

pytest never opens static/index.html, so a stray brace there passes CI and
breaks the app. Uses node when it is available and falls back to a bracket
balance check when it is not, so this is never a reason a machine cannot run
the checks.

The second check is the one paid for in a bug: two top-level `addTypedTopic`
declarations, four thousand lines apart, one for the DailyFAM picker and one
for the interests catalogue. That is legal JavaScript - the later declaration
simply wins - so it parses, it lints, and the earlier screen's button is
inert. Every control in this interface is an inline `onclick` naming a global,
so a duplicated top-level name is a button that quietly does the wrong thing
or nothing at all, with no error anywhere. Nothing else here can see it:
`node --check` is happy, the smoke test only clicks what somebody thought to
click, and the failure is silent by construction.

The subject is read out of the sources rather than listed here, because a
guard whose subject is enumerated by hand is decorative.
"""
from __future__ import annotations

import collections
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [ROOT / "static" / "index.html",
           # The share landing page. Its script is the only thing
           # standing between a stranger and silence, and nothing else
           # here opens it - the preview builds render the app.
           ROOT / "static" / "listen.html"]
PLAIN_JS = [ROOT / "static" / "fam-audio.js"]

INLINE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
DECLARATION = re.compile(r"(?m)^([ \t]*)function\s+(\w+)\s*\(")


def duplicate_top_level_names(code: str) -> list[tuple[str, int]]:
    """Names declared more than once at the shallowest function depth.

    Indentation stands in for scope. That is a proxy rather than a parse, and
    it is deliberately narrowed to the *shallowest* indent any function in the
    source is declared at - the top level, where a redeclaration is a global
    silently replaced and every inline handler in the markup follows the
    survivor. A nested function shadowing a name is ordinary and is not
    flagged; two globals sharing one is the bug.
    """
    seen = DECLARATION.findall(code)
    if not seen:
        return []
    top = min(len(indent.expandtabs(2)) for indent, _ in seen)
    counts = collections.Counter(
        name for indent, name in seen if len(indent.expandtabs(2)) == top)
    return sorted((name, n) for name, n in counts.items() if n > 1)


def sources() -> list[tuple[str, str]]:
    out = [(str(p.relative_to(ROOT)), p.read_text(encoding="utf-8")) for p in PLAIN_JS]
    for path in TARGETS:
        html = path.read_text(encoding="utf-8")
        blocks = INLINE.findall(html)
        if not blocks:
            raise SystemExit(f"{path.name}: no inline script found - has the file changed?")
        out.append((f"{path.name} (inline)", "\n".join(blocks)))
    return out


def main() -> int:
    node = shutil.which("node")
    failures = []
    for name, code in sources():
        if node:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
                fh.write(code)
                tmp = fh.name
            result = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
            pathlib.Path(tmp).unlink(missing_ok=True)
            if result.returncode != 0:
                failures.append(f"{name}:\n{result.stderr.strip()}")
        else:
            depth = {"{": 0, "(": 0, "[": 0}
            pairs = {"}": "{", ")": "(", "]": "["}
            for ch in re.sub(r"//[^\n]*|/\*.*?\*/", "", code, flags=re.S):
                if ch in depth:
                    depth[ch] += 1
                elif ch in pairs:
                    depth[pairs[ch]] -= 1
            bad = [k for k, v in depth.items() if v != 0]
            if bad:
                failures.append(f"{name}: unbalanced {', '.join(bad)} (node not installed, "
                                "so this is only a balance check)")
        for dupe, times in duplicate_top_level_names(code):
            failures.append(
                f"{name}: `function {dupe}` is declared {times} times at the top level. "
                "The last one wins and the others never run, so every inline "
                "onclick naming it calls whichever came last - silently. Give "
                "each one a name that says which screen it serves.")
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        return 1
    print(f"checked {len(sources())} script source(s): OK"
          + ("" if node else "  [balance check only - node not installed]"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
