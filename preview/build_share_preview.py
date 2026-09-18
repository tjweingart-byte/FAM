"""The share landing page, on a phone, without a server.

`/s/<id>` is the one surface in FAM that is deliberately *not* the app: a
stranger who was sent one episode gets that episode and no way into anything
else. It is therefore not part of `build_preview.py`, which ships
`static/index.html` - a different page, for a different person.

This builds the real `static/listen.html` the way the server builds it -
through `sharing.render_landing`, from a fixture share - and swaps only the two
things a published page cannot have:

* **`/api/audio`** returns silence of the right length, exactly as the other
  two preview builds do. No script is written and nothing is spoken.
* **`/api/share/<id>/open`** returns `{ok: true}`. There is nothing to count.

Everything else is the shipped page: the markup, the CSS, the player, and the
doors. Which is the point - what a reviewer is judging here is whether the
landing page reads as one episode rather than as an app, and that is exactly
what a phone can show and a test cannot.

    python preview/build_share_preview.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sharing  # noqa: E402

OUT = ROOT / "preview" / "fam-share-landing.html"

#: A share that reads like a real one. The App Store link is set, because the
#: thing worth looking at on a phone is the page *with* its doors - the empty
#: state is one paragraph and is covered by a test.
FIXTURE = {
    "id": "preview-share",
    "title": "Who Makes the Chips",
    "query": "why is semiconductor manufacturing so concentrated in taiwan",
    "minutes": 3,
}
APP_STORE = "https://apps.apple.com/app/fam/id0000000000"

SHIM = """
<script>
/* The two things a published page cannot do. Same silence generator as the
   other preview builds, so the transport, the scrub bar and the clock behave
   exactly as they do against a real server - only the voice is missing. */
(function () {
  var SAMPLE_RATE = 24000;
  var realFetch = window.fetch.bind(window);
  function silence(seconds) {
    var total = Math.round(seconds * SAMPLE_RATE), sent = 0;
    var stream = new ReadableStream({
      pull: function (c) {
        if (sent >= total) { c.close(); return; }
        var n = Math.min(SAMPLE_RATE, total - sent);
        sent += n;
        c.enqueue(new Uint8Array(n * 2));
        return new Promise(function (r) { setTimeout(r, 60); });
      }
    });
    return Promise.resolve(new Response(stream, {
      status: 200,
      headers: { "Content-Type": "audio/L16", "X-Sample-Rate": String(SAMPLE_RATE) }
    }));
  }
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    if (url.indexOf("/api/audio") === 0) {
      var qs = new URLSearchParams(url.split("?")[1] || "");
      return silence(Math.max(1, Number(qs.get("minutes") || 1)) * 60);
    }
    if (url.indexOf("/api/share/") === 0) {
      return Promise.resolve(new Response('{"ok":true}',
        { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return realFetch(input, init);
  };
})();
</script>
<div style="position:fixed;left:0;right:0;bottom:0;z-index:99;padding:7px 12px;
            background:#18151F;color:#ABA3C4;font:10px/1.4 'JetBrains Mono',monospace;
            text-align:center;border-top:1px solid rgba(244,239,228,0.14)">
  PREVIEW - the page is real, the voice is silence. A server speaks it.
</div>
"""


def build() -> pathlib.Path:
    template = (ROOT / "static" / "listen.html").read_text(encoding="utf-8")
    payload = sharing.landing_payload(
        FIXTURE,
        url="https://fam.audio/s/preview-share",
        card_url="https://fam.audio/api/share/card?share=preview-share",
        app_store=APP_STORE,
    )
    page = sharing.render_landing(template, payload)

    # The player is a separate file on the server and there is nothing to
    # serve it here, so it is inlined. Read rather than re-implemented: the
    # retained Int16 buffer, the clock-derived cursor and TAIL_MARGIN were all
    # paid for in bugs, and a preview running a second player would be
    # previewing something FAM does not ship.
    audio = (ROOT / "static" / "fam-audio.js").read_text(encoding="utf-8")
    page = page.replace('<script src="/fam-audio.js"></script>',
                        "<script>\n" + audio + "\n</script>")
    page = page.replace("</body>", SHIM + "</body>")

    OUT.write_text(page, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB)")
