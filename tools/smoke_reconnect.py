#!/usr/bin/env python3
"""A stream that breaks part-way, in a real browser (PROBLEMS.md §685).

The 11.16 report: "My episode paused 2/3 of the way through and did not
continue" (Android web). Before §685 a broken stream stopped the web player
at once - the audio already here thrown away - and nothing asked again. This
drives `static/fam-audio.js` against a tiny server whose `/api/audio` sends
part of an episode as chunked PCM and then cuts the connection without the
closing chunk (a dropped connection, not an ending):

1. **Kept episode.** The reconnect asks by the episode's id
   (`episode=`, `reconnect=1`, `fmt=pcm`), skips what is already here, and
   the player ends holding exactly the whole episode, then calls `onEnd`.
2. **Not kept.** The reconnect is answered 409: the player plays out
   everything that arrived, *then* calls `onError` with `brokeOff`.
3. **No episode id.** Nothing is asked again; the same play-out.

And `playbackLog()` says each step, which Report a problem sends.

No FAM server, no key, nothing spent.

    python tools/smoke_reconnect.py
"""
from __future__ import annotations

import http.server
import math
import pathlib
import socketserver
import struct
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from smoke_opus import launch_browser  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RATE = 8000
WHOLE = RATE * 3           # a three-second episode
BREAK_AT = RATE * 1        # the first stream breaks after one second
EPISODE = "abc123"


def tone() -> bytes:
    return b"".join(struct.pack("<h", int(8000 * math.sin(i / 9.0)))
                    for i in range(WHOLE))


PCM = tone()
ASKED: list[str] = []
MODE = {"value": "kept"}

PAGE = """<!doctype html><meta charset=utf-8><body>
<script src="/fam-audio.js"></script>
<script>
window.result = null;
window.go = function(){
  FamAudio.play("q", 1, {
    onEnd: function(){ window.result = {end: true, samples: FamAudio.duration(),
                                       log: FamAudio.playbackLog()}; },
    onError: function(e){ window.result = {error: e.message, brokeOff: !!e.brokeOff,
                                          heard: FamAudio.position(),
                                          samples: FamAudio.duration(),
                                          log: FamAudio.playbackLog()}; }
  });
};
</script>"""


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/fam-audio.js":
            body = (ROOT / "static" / "fam-audio.js").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path != "/api/audio":
            self.send_error(404)
            return
        ASKED.append(self.path)
        query = urllib.parse.parse_qs(url.query)
        if query.get("reconnect") == ["1"]:
            if MODE["value"] == "gone":
                body = b'{"detail":"That episode is no longer kept."}'
                self.send_response(409)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/L16")
            self.send_header("X-Sample-Rate", str(RATE))
            self.send_header("Content-Length", str(len(PCM)))
            self.end_headers()
            self.wfile.write(PCM)  # the whole episode; the player skips its front
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/L16")
        self.send_header("X-Sample-Rate", str(RATE))
        self.send_header("X-FAM-Cache", "hit")
        if MODE["value"] != "anonymous":
            self.send_header("X-FAM-Episode", EPISODE)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        part = PCM[:BREAK_AT * 2]
        self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
        self.wfile.flush()
        time.sleep(0.5)  # let the part reach the page before the cut does
        # Cut without the closing chunk: the browser reads a network error.
        self.close_connection = True
        self.connection.shutdown(2)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main() -> int:
    from playwright.sync_api import sync_playwright

    server = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"
    failures = []

    def check(ok, what):
        print(("  ok   " if ok else "  FAIL ") + what)
        if not ok:
            failures.append(what)

    with sync_playwright() as pw:
        browser = launch_browser(pw)
        for mode in ("kept", "gone", "anonymous"):
            MODE["value"] = mode
            ASKED.clear()
            page = browser.new_page()
            page.goto(base)
            page.evaluate("go()")
            page.wait_for_function("window.result !== null", timeout=30000)
            result = page.evaluate("window.result")
            page.close()
            log = result.get("log") or ""
            again = [a for a in ASKED if "reconnect=1" in a]
            print(f"{mode}:")
            if "-v" in sys.argv:
                print(result)
            if mode == "kept":
                check(result.get("end") is True, "a kept episode reconnects and ends")
                check(abs(result.get("samples", 0) - WHOLE / RATE) < 0.01,
                      f"holding exactly the whole episode ({result.get('samples')}s)")
                check(len(again) == 1 and f"episode={EPISODE}" in again[0]
                      and "fmt=pcm" in again[0],
                      "asked again once, by the episode's id, as PCM")
                check("stream broke at 1.0s" in log and "reconnected" in log,
                      "the log says it broke and reconnected")
            else:
                check(result.get("brokeOff") is True, "told it broke off")
                check(abs(result.get("samples", 0) - BREAK_AT / RATE) < 0.01,
                      "holding what arrived")
                check(result.get("heard", 0) >= BREAK_AT / RATE - 0.1,
                      f"after playing all of it ({result.get('heard')}s heard)")
                check(len(again) == (1 if mode == "gone" else 0),
                      "asked again only when the stream named its episode")
                check("playing out" in log, "the log says it played out")
        browser.close()
    server.shutdown()
    if failures:
        print(f"{len(failures)} reconnect check(s) failed")
        return 1
    print("all reconnect checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
