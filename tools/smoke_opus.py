#!/usr/bin/env python3
"""Opus to the browser, in a real browser (PROBLEMS.md §242).

The server half of `fmt=opus` is unit-tested (`tests/test_opus_stream_242.py`);
the half that decodes it - WebCodecs in `static/fam-audio.js` - only exists in
a browser, so it is driven in one. A zero-spend server is started at 24 kHz
(the rate Opus carries; the development engine's 22.05 kHz would quietly fall
back to PCM and prove nothing), the app is opened, and one episode is played
through `FamAudio` and compared with the same episode fetched as PCM:

* the player asked for Opus, and the server answered with it;
* the player ends holding exactly as many samples as PCM carries - the
  pre-skip dropped and the end marker honoured - so no caption can drift;
* Opus is a small fraction of PCM's bytes, and first audio still arrives.

Every store goes to a temporary directory, found by reading every
`data_path("VAR", ...)` in the source rather than listed here: a list kept by
hand is how a new store ends up written into the working tree.

And a browser with **no Opus decoder of its own** (§292) - Chromium with
`AudioDecoder` removed, standing in for Safari: it must still ask for a link,
read the Ogg Opus file whole from the bucket (here a route that answers for
R2), and hold exactly the file's samples without streaming anything else.

    python tools/smoke_opus.py
"""
from __future__ import annotations

import os
import pathlib
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUESTION = "why is the ocean salty"


def launch_browser(pw):
    """Playwright's own download first; any installed Chromium after (as in
    `smoke_landing.py`, and duplicated for the same reason)."""
    args = ["--autoplay-policy=no-user-gesture-required"]
    candidates = [os.environ.get("PLAYWRIGHT_CHROMIUM")]
    try:
        return pw.chromium.launch(args=args)
    except Exception as first:
        for pattern in ("opt/pw-browsers/chromium-*/chrome-linux/chrome",
                        "opt/pw-browsers/chromium/chrome-linux/chrome"):
            candidates += sorted(str(p) for p in pathlib.Path("/").glob(pattern))
        for path in [c for c in candidates if c and pathlib.Path(c).exists()]:
            try:
                return pw.chromium.launch(executable_path=path, args=args)
            except Exception:
                continue
        raise first


def store_variables() -> dict:
    found = {}
    for source in ROOT.glob("*.py"):
        for var, name in re.findall(r'data_path\(\s*"([A-Z0-9_]+)",\s*"([^"]+)"',
                                    source.read_text(errors="ignore")):
            found[var] = name
    return found


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


BUCKET_FILE = "https://bucket.fam.test/recent/whole.opus"


def kept_file(seconds: float = 2.0, rate: int = 24000) -> tuple[bytes, int]:
    """An Ogg Opus file packed exactly as kept audio is, and its length."""
    import math

    sys.path.insert(0, str(ROOT))
    import audio_codec

    frames = int(seconds * rate)
    pcm = b"".join(int(6000 * math.sin(2 * math.pi * 220 * i / rate)).to_bytes(
        2, "little", signed=True) for i in range(frames))
    codec, blob = audio_codec.encode(pcm, rate)
    assert codec == audio_codec.OPUS, codec
    return blob, frames


def whole_file(browser, base: str, check) -> None:
    """§292: a replay on a browser that decodes Opus only as a whole file."""
    import json

    blob, frames = kept_file()
    page = browser.new_page()
    page.add_init_script("try { delete window.AudioDecoder; } catch (e) {}"
                         " window.AudioDecoder = undefined;")
    asked, read = [], []

    def link(route):
        asked.append(route.request.url)
        if "link=1" not in route.request.url:
            return route.continue_()
        route.fulfill(status=200, content_type="application/json", headers={
            "X-FAM-Audio-Link": "1", "X-Sample-Rate": "24000", "X-FAM-Cache": "hit",
            "X-FAM-Episode": "whole-file-smoke", "Access-Control-Expose-Headers":
            "X-FAM-Audio-Link, X-Sample-Rate, X-FAM-Cache, X-FAM-Episode"},
            body=json.dumps({"link": BUCKET_FILE, "format": "ogg-opus",
                             "frames": frames, "sentences": ["One."], "starts": [0.0]}))

    def bucket(route):
        read.append(route.request.url)
        route.fulfill(status=200, body=blob, content_type="audio/ogg",
                      headers={"Access-Control-Allow-Origin": "*"})

    page.route("**/api/audio?*", link)
    page.route(BUCKET_FILE, bucket)
    page.goto(base + "/")
    page.wait_for_function("window.FamAudio !== undefined")
    played = page.evaluate("""(q) => new Promise((resolve) => {
        FamAudio.play(q, 1, {
            onError: (e) => resolve({error: String(e && e.message || e)}),
        });
        const poll = setInterval(() => {
            if (FamAudio.isComplete()) { clearInterval(poll);
                resolve({samples: Math.round(FamAudio.duration() * 24000)}); }
        }, 100);
        setTimeout(() => resolve({error: 'timed out'}), 30000);
    })""", QUESTION)
    page.close()
    check("without WebCodecs the player asks for PCM with a link",
          any("fmt=pcm&link=1" in u for u in asked), str(asked))
    check("it reads the file from the bucket", read == [BUCKET_FILE], str(read))
    check("it plays the whole file without an error", "error" not in played, str(played))
    check("it holds exactly the file's samples", played.get("samples") == frames,
          f"{played} vs {frames}")
    check("nothing was streamed instead",
          not any("link_failed" in u for u in asked), str(asked))


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(("  ok    " if ok else "  FAIL  ") + name
              + (f"  [{detail}]" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as data:
        env = dict(os.environ, FAM_ENV="staging", SAMPLE_RATE="24000",
                   READ_LIMIT_PER_WINDOW="0", AUDIO_STREAM_OPUS="1")
        for var, name in store_variables().items():
            env[var] = os.path.join(data, name)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
             "--port", str(port)], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(120):
                try:
                    urllib.request.urlopen(base + "/api/auth/me", timeout=2)
                    break
                except Exception:
                    time.sleep(0.5)
            else:
                print("  FAIL  the server did not start", file=sys.stderr)
                return 1

            with sync_playwright() as pw:
                browser = launch_browser(pw)
                whole_file(browser, base, check)
                page = browser.new_page()
                asked = []
                page.on("request", lambda r: asked.append(r.url)
                        if "/api/audio" in r.url else None)
                page.goto(base + "/")
                page.wait_for_function("window.FamAudio !== undefined")
                supported = page.evaluate(
                    "typeof AudioDecoder === 'function' ? AudioDecoder.isConfigSupported("
                    "{codec:'opus', sampleRate:24000, numberOfChannels:1})"
                    ".then(r => r.supported) : false")
                if not supported:
                    print("  SKIPPED: this browser cannot decode Opus; the player "
                          "uses PCM here, which the preview smoke tests cover.")
                    return 1 if failures else 0
                page.evaluate("fetch('/api/consent', {method:'POST', headers:"
                              "{'content-type':'application/json'},"
                              "body: JSON.stringify({allow: true})})")
                ref = page.evaluate("""async (q) => {
                    const r = await fetch('/api/audio?q=' + encodeURIComponent(q)
                                          + '&minutes=1&fmt=pcm');
                    return (await r.arrayBuffer()).byteLength / 2; }""", QUESTION)
                opus_bytes = page.evaluate("""async (q) => {
                    const r = await fetch('/api/audio?q=' + encodeURIComponent(q)
                                          + '&minutes=1&fmt=opus');
                    return [r.headers.get('X-FAM-Audio-Format'),
                            (await r.arrayBuffer()).byteLength]; }""", QUESTION)
                played = page.evaluate("""(q) => new Promise((resolve) => {
                    let first = null; const t0 = performance.now();
                    FamAudio.play(q, 1, {
                        onFirstAudio: () => { first = performance.now() - t0; },
                        onError: (e) => resolve({error: String(e && e.message || e)}),
                    });
                    const poll = setInterval(() => {
                        if (FamAudio.isComplete()) { clearInterval(poll);
                            resolve({first: first,
                                     samples: Math.round(FamAudio.duration() * 24000)}); }
                    }, 100);
                    setTimeout(() => resolve({error: 'timed out'}), 60000);
                })""", QUESTION)
                browser.close()

            player_asked = [u for u in asked if "fmt=opus" in u and "fmt=pcm" not in u]
            check("the player asks for Opus where the browser decodes it",
                  bool(player_asked), str(asked))
            check("the server answers a request for Opus with Opus",
                  opus_bytes[0] == "opus", str(opus_bytes))
            check("the player plays without an error", "error" not in played, str(played))
            check("it holds exactly the samples PCM carries",
                  played.get("samples") == round(ref), f"{played} vs {ref}")
            check("Opus is under a tenth of PCM's bytes",
                  opus_bytes[1] < ref * 2 / 10, f"{opus_bytes[1]} vs {ref * 2}")
            check("first audio arrives", played.get("first") is not None, str(played))
        finally:
            server.terminate()
            server.wait(timeout=10)

    if failures:
        print(f"{len(failures)} Opus check(s) failed")
        return 1
    print("all Opus checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
