"""One command that walks the whole voice chain and says which link is broken.

    python tools/voice_doctor.py
    python tools/voice_doctor.py --speak        # and make it produce audio
    python tools/voice_doctor.py --json         # for a script or a CI step

The failure this exists for does not arrive as a useful sentence. It arrives as
`404 Not Found` in the middle of an episode, and the address that produced it
has three halves that can be wrong, sitting on two machines nobody can see at
once: Render holds an address, RunPod holds a pod, the pod holds a mode and a
port, and the image on it holds a version of this repo. Finding out which one
moved has taken a day more than once (PROBLEMS.md §112).

So this asks every question in the chain, in order, on one screen:

    1. What this app is configured to do          VOICE_BACKEND and the rest
    2. How it will look for a worker              voice_control.ladder()
    3. What that search actually finds            the registry, RunPod's API
    4. Whether each candidate is really there     a real /health
    5. Which build is answering                   contract, image, commit
    6. Whether it can actually speak              --speak, real audio

Every failure prints the fix beside it, because the answer to "the worker is in
serverless mode" is one environment variable and nobody should have to go and
find out which.

Exit codes: 0 everything checked passed; 1 a worker answered but cannot serve;
2 nothing could be found at all; 3 this app is not configured for a remote
voice in the first place.

It reads the same `voice_control.ladder()` the running app walks, so it cannot
describe an order the app does not use - which is the whole reason it is not a
checklist in a document.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import remote_voice  # noqa: E402
import voice_control  # noqa: E402
from config import settings  # noqa: E402

BOLD, DIM, RED, GREEN, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")


def _say(mark: str, colour: str, line: str) -> None:
    print(f"  {colour}{mark}{RESET} {line}")


def _fix(*lines: str) -> None:
    for line in lines:
        print(f"      {DIM}{line}{RESET}")


async def run(speak: bool, text: str) -> tuple[int, dict]:
    findings: dict = {"backend": settings.voice_backend}

    # 1. Is this app even meant to be talking to a GPU somewhere else?
    print(f"\n{BOLD}1. What this app is configured to do{RESET}")
    if settings.voice_backend != "remote":
        _say("!", YELLOW, f"VOICE_BACKEND={settings.voice_backend}: the voice "
                          "runs in this process, so none of the rest applies")
        _fix("Set VOICE_BACKEND=remote to use a worker on another machine,",
             "or run `python verify_voice.py` to check the card in this one.")
        return 3, findings
    _say("ok", GREEN, f"VOICE_BACKEND=remote, transport "
                      f"{settings.remote_voice_transport}, "
                      f"{settings.remote_voice_sample_rate} Hz expected")
    _say("-", DIM, f"discovery: {'auto' if voice_control.discovery_enabled() else 'off'}"
                   f", contract {voice_control.CONTRACT_VERSION}")
    # Printed here rather than left to be inferred from a rung that is missing,
    # because on a pod it decides whether the *only* address a server can use
    # is on the ladder at all (PROBLEMS.md §116).
    if voice_control.allow_plain_http():
        _say("-", DIM, "plain HTTP is allowed, so a pod's direct TCP address "
                       "may be used")
    else:
        _say("-", DIM, "plain HTTP is refused (VOICE_ALLOW_PLAIN_HTTP=0), so "
                       "only TLS addresses are candidates - on RunPod that is "
                       "the Cloudflare-fronted proxy and nothing else")
    findings["plain_http"] = ("allowed" if voice_control.allow_plain_http()
                              else "refused")

    # 2. The ladder. Printed even where a rung is switched off, because "there
    #    is no ladder" and "every rung is unset" are different diagnoses.
    print(f"\n{BOLD}2. How it will look for a worker{RESET}")
    rungs = voice_control.ladder()
    findings["ladder"] = [rung.as_dict() for rung in rungs]
    for rung in rungs:
        if rung.configured:
            _say("ok", GREEN, f"{rung.name}: {rung.detail}")
        else:
            _say("-", DIM, f"{rung.name}: {rung.detail}")
    if not [r for r in rungs if r.configured]:
        print(f"\n{RED}No rung is configured, so nothing can be found.{RESET}")
        _fix("Set one of:",
             "  REMOTE_VOICE_URL=https://<pod id>-8001.proxy.runpod.net",
             "  RUNPOD_ENDPOINT_ID=<id> with RUNPOD_API_KEY",
             "  RUNPOD_POD=<pod name> with RUNPOD_API_KEY",
             "  VOICE_REGISTRY_TOKEN, and FAM_APP_URL on the pod",
             "See REMOTE_VOICE.md.")
        return 2, findings

    # 3. What the search finds. This is where a stale address stops being
    #    invisible: a pinned URL and a registered one that disagree are printed
    #    next to each other.
    print(f"\n{BOLD}3. What that search finds right now{RESET}")
    candidates = await voice_control.candidates()
    findings["candidates"] = [c.as_dict() for c in candidates]
    if not candidates:
        print(f"\n{RED}Nothing was found at any rung.{RESET}")
        _pod_notes()
        _fix("A configured rung that finds nothing means the thing it names is",
             "gone: a pod that was destroyed, an endpoint id that was deleted,",
             "or a registration that expired (VOICE_REGISTRY_TTL).",
             "Check the pod list on RunPod before changing anything here.")
        return 2, findings
    for candidate in candidates:
        _say("-", DIM, f"{candidate.rung}: {candidate.url}  ({candidate.why})")
    _pod_notes()
    _stale_warning(candidates)

    # 4 and 5. Is it there, and which build is it.
    print(f"\n{BOLD}4. Whether each one is actually there{RESET}")
    verdicts = []
    for candidate in candidates:
        verdict = await voice_control.verify(candidate)
        verdicts.append((candidate, verdict))
        findings.setdefault("verdicts", []).append(
            {**candidate.as_dict(), "verdict": verdict.as_dict()})
        if verdict.ok:
            build = " / ".join(part for part in (
                f"contract {verdict.contract}" if verdict.contract else "",
                f"{verdict.sample_rate} Hz" if verdict.sample_rate else "",
                verdict.image, verdict.commit[:7] if verdict.commit else "",
            ) if part)
            _say("ok", GREEN, f"{candidate.url}: {verdict.detail}"
                              + (f"  [{build}]" if build else ""))
            _contract_note(verdict)
        else:
            _say("x", RED, f"{candidate.url}: {verdict.detail}")
            _explain(candidate, verdict)

    working = [(c, v) for c, v in verdicts if v.ok]
    if not working:
        print(f"\n{RED}Every candidate answered something other than "
              f"'I can speak'.{RESET}")
        return 1, findings

    chosen, verdict = working[0]
    print(f"\n{BOLD}5. What the app would use{RESET}")
    _say("ok", GREEN, f"{chosen.rung}: {chosen.url}  ({chosen.why})")
    findings["chosen"] = chosen.as_dict()
    if chosen.rung != candidates[0].rung:
        _say("!", YELLOW, f"the {candidates[0].rung} rung is ahead of it and "
                          "is not answering; that is the thing to fix, even "
                          "though the voice works")

    # 6. The only question that cannot be answered by reading. Off by default
    #    on purpose: on a serverless endpoint it wakes a worker and bills for
    #    the boot, which is not something a diagnostic should do unasked.
    if not speak:
        print(f"\n{DIM}Not asking it to speak. Add --speak to make real audio; "
              f"on a serverless endpoint that pays a cold start.{RESET}")
        return 0, findings

    print(f"\n{BOLD}6. Whether it can actually speak{RESET}")
    try:
        pcm = await _synthesise(chosen, text)
    except Exception as exc:
        _say("x", RED, f"{type(exc).__name__}: {exc}")
        _fix("A worker whose /health is green and whose /synth fails is the",
             "case the control plane cannot see in advance. The app will",
             "demote this endpoint on the first failed chunk and re-resolve,",
             "so check the rung below it too.")
        findings["spoke"] = False
        return 1, findings
    seconds = len(pcm) / float(settings.remote_voice_sample_rate * 2)
    _say("ok", GREEN, f"{len(pcm)} bytes, {seconds:.1f}s of audio at "
                      f"{settings.remote_voice_sample_rate} Hz")
    findings["spoke"] = True
    findings["audio_seconds"] = round(seconds, 2)
    return 0, findings


def _pod_notes() -> None:
    """What RunPod said about pods that did not become candidates.

    Usually the whole diagnosis, and unambiguous since the nightly schedule
    was removed (§117): nothing stops this pod on purpose any more, so a pod
    that is found and not running is always something to act on rather than a
    clock somebody set.
    """
    for note in voice_control.report().get("pods") or []:
        _say("!", YELLOW, note)


def _stale_warning(candidates: list) -> None:
    """Say when the pinned address is not the one the workers are announcing.

    The single most useful line this tool can print. It is the exact state that
    produced the 404: `REMOTE_VOICE_URL` still names the pod that was replaced,
    and the pod that replaced it has been introducing itself for an hour.
    """
    pinned = [c for c in candidates if c.rung == "pinned"]
    others = [c for c in candidates if c.rung in ("registered", "runpod-pod")]
    if not pinned or not others:
        return
    if any(c.url == pinned[0].url for c in others):
        return
    _say("!", YELLOW,
         f"REMOTE_VOICE_URL names {pinned[0].url}, but the worker that is "
         f"announcing itself is at {others[0].url}. If the first one is dead, "
         "the app now fails over to the second - but clear REMOTE_VOICE_URL "
         "so the ladder stops starting with an address that has moved.")


def _contract_note(verdict) -> None:
    if verdict.contract and verdict.contract != voice_control.CONTRACT_VERSION:
        _say("!", YELLOW,
             f"this worker speaks contract {verdict.contract}; this app is "
             f"{voice_control.CONTRACT_VERSION}. It is not refused - an older "
             "worker that serves /synth is a working voice - but rebuild the "
             "image from Dockerfile.voice when convenient.")


def _explain(candidate, verdict) -> None:
    """The fix, beside the failure, in the words of whatever went wrong."""
    detail = verdict.detail.lower()
    if "404" in detail or "no port" in detail or "answers /health" in detail:
        _fix("Both causes are on the pod, and neither is visible from here:",
             "  PORT must match the port in the proxy URL; the container",
             "    serves ${PORT:-8001}.",
             "  An image built before this was fixed defaults to the",
             "    serverless handler, which opens no port at all. Rebuild",
             "    from Dockerfile.voice - it now picks the mode from the",
             "    platform - or set VOICE_WORKER_MODE=http on the pod.",
             f"  python tools/probe_remote_voice.py --url {candidate.url}")
    elif "401" in detail or "token" in detail or "rejected" in detail:
        _fix("The bearer token the app sends and the one the worker checks are",
             "different. REMOTE_VOICE_TOKEN on both sides, RUNPOD_API_KEY for",
             "a serverless endpoint.")
    elif "hz" in detail:
        _fix("The stream header is written before any audio exists, so the",
             "rate cannot be corrected later. Set REMOTE_VOICE_SAMPLE_RATE to",
             "what the worker emits, and restart this app.")
    elif "cannot speak" in detail or "rights" in detail or "gpu" in detail:
        _fix("The worker is running and refusing, which is it telling you",
             "something true: no GPU on the node, or reference_3.wav and its",
             ".rights.json are not on the network volume at /state/voices.")
    else:
        _fix(f"python tools/probe_remote_voice.py --url {candidate.url}",
             "asks the worker itself what it serves.")


async def _synthesise(endpoint, text: str) -> bytes:
    """Speak through the real engine, so this checks what a listener gets.

    Not a hand-rolled POST: the decode, the sample-rate check and the
    whole-sample check all live in `remote_voice`, and a diagnostic that
    reimplemented them would be able to pass while the app fails.
    """
    engine = remote_voice.RemoteChatterboxEngine()
    config = engine.config()
    payload = engine._payload(text, config)
    output = await engine._speak(payload, config, endpoint)
    return engine._pcm_from(output, config)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--speak", action="store_true",
                        help="make real audio; on serverless this pays a cold start")
    parser.add_argument("--text", default="This is FAM, checking the voice.")
    parser.add_argument("--json", action="store_true",
                        help="print the findings as JSON and nothing else")
    args = parser.parse_args()

    if args.json:
        # Everything the human-readable run prints goes to stderr, so the JSON
        # on stdout is a machine's to parse.
        sys.stdout, real = sys.stderr, sys.stdout
        try:
            code, findings = asyncio.run(run(args.speak, args.text))
        finally:
            sys.stdout = real
        print(json.dumps(findings, indent=2))
        return code

    code, _ = asyncio.run(run(args.speak, args.text))
    print()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
