"""Does the voice hear stress? Say names four ways and listen (PROBLEMS.md §549).

`pronunciation.voiced` hands Chatterbox every respelling in lower case, so
"sah-gah-poh-loo-TEH-leh" arrives as "Sah-gah-poh-loo-teh-leh" and the stress the
lexicon records never reaches the voice. This says each name four ways
(`pronounce_review.stress_variants`) - as now, run together, with an accent
on the stressed vowel, and syllables apart with the stressed one
capitalised - bare and in a sentence, and writes a page to listen to them.

    python tools/stress_trial.py --csv fam-pronunciations-2026-11-11.csv
    python tools/stress_trial.py --names "Sagapolutele,Wembanyama" --limit 12
    python tools/stress_trial.py --names "Sagapolutele=sah-gah-poh-loo-TEH-leh"

Names come from a download of /admin's list (`--csv`), else this machine's
lexicon. Only names with a stressed syllable marked and three or more
syllables are tried, longest first, unless `--names` picks them. Needs a
voice that speaks here: the remote worker (REMOTE_VOICE_URL and its token
from the credential chain) or Chatterbox in this process. Keeps nothing in
the lexicon or the voice checks.

Capitals (§566): `--caps` says words in capitals five ways instead - letters
apart ("F B I"), dotted, the letters' names ("eff bee eye"), as written
("NASCAR") and in ordinary case ("Nascar") - and marks the one the voice is
handed today. Which way an initialism and a word each sound smoothest
decides `spoken_text`'s spelling and `WORD_CASE`.

    python tools/stress_trial.py --caps
    python tools/stress_trial.py --caps "NASCAR,NVIDIA,FBI,ESPN"

Writes <out>/index.html (open it and listen), <out>/results.csv and the WAVs.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import html
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pronounce_review  # noqa: E402
import pronunciation  # noqa: E402

#: `--caps` with no list: words said as words and initialisms, from the news.
CAPS_DEFAULT = "NASCAR,NVIDIA,NATO,DOGE,EBITDA,FBI,ESPN,NCAA,NFL,UCLA"

_STRESS_HEADS = {"now": "As now (no stress)", "joined": "Run together",
                 "accent": "Accent on the stress",
                 "spaced": "Syllables apart, stress capitalised"}
_CAPS_HEADS = {"spaced": "Letters apart (F B I)", "dotted": "Dotted (F.B.I.)",
               "named": "Letter names (eff bee eye)", "upper": "As written (NASCAR)",
               "title": "Ordinary case (Nascar)"}


def _syllables(say: str) -> int:
    return sum(len([s for s in w.split("-") if s]) for w in say.split())


def pick(rows: list[tuple[str, str]], limit: int) -> list[tuple[str, str]]:
    """The names most likely to show whether stress matters."""
    keep = [(n, s) for n, s in rows
            if pronounce_review.has_stress(s) and _syllables(s) >= 3]
    keep.sort(key=lambda r: (-_syllables(r[1]), r[0].lower()))
    return keep[:limit]


def _from_csv(path: str) -> list[tuple[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [(r["name"], r.get("current_say") or r.get("say") or "")
                for r in csv.DictReader(fh) if r.get("name")]


def _page(results: list[dict], caps: bool = False) -> str:
    rows = []
    for r in results:
        cells = []
        for v in r["variants"]:
            score = "" if v["score"] is None else f" · {round(v['score'] * 100)}% alike"
            today = " <b>(today)</b>" if v["variant"] == r.get("today") else ""
            cells.append(
                f"<td{' style=background:#fff6d6' if today else ''}>"
                f"<code>{html.escape(v['sent'])}</code>{today}<br>"
                f"<audio controls src='{html.escape(v['file'])}'></audio><br>"
                f"<small>heard <code>{html.escape(v['heard_say'] or '-')}</code>{score}</small></td>")
        rows.append(f"<tr><th>{html.escape(r['name'])}<br><small>{html.escape(r['say'])}"
                    f"{' · in a sentence' if r['sentence'] else ''}</small></th>{''.join(cells)}</tr>")
    heads = "".join(f"<th>{html.escape(h)}</th>" for h in (
        _CAPS_HEADS if caps else _STRESS_HEADS).values())
    title, ask = (("Capitals trial", "Which column says each word smoothly, the way a "
                   "broadcaster would? <b>(today)</b> marks what the voice is handed now. "
                   "Note the best column for the words and the best for the initialisms.")
                  if caps else
                  ("Stress trial", "Which column says the stressed syllable (the capitals "
                   "in the respelling) where it belongs?"))
    return (f"<!doctype html><meta charset=utf-8><title>{title}</title>"
            "<style>body{font:14px system-ui;margin:16px}td,th{border:1px solid #ccc;"
            "padding:6px;vertical-align:top;text-align:left}table{border-collapse:collapse}"
            f"audio{{width:220px}}</style><h1>{title}</h1>"
            f"<p>{ask} The % is a phoneme recogniser's guess; trust your ears.</p>"
            f"<table><tr><th>{'Word' if caps else 'Name'}</th>{heads}</tr>{''.join(rows)}</table>")


async def run(names: list[tuple[str, str]], out: pathlib.Path, sentences: bool,
              caps: bool = False) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for i, (name, say) in enumerate(names, 1):
        for sentence in ((False, True) if sentences else (False,)):
            print(f"  [{i}/{len(names)}] {name}{'' if caps else ' = ' + say}"
                  f"{' (sentence)' if sentence else ''}", flush=True)
            variants = (await pronounce_review.hear_caps(name, sentence=sentence) if caps
                        else await pronounce_review.hear_variants(name, say=say, sentence=sentence))
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            for v in variants:
                v["file"] = f"{slug}{'-sentence' if sentence else ''}-{v['variant']}.wav"
                (out / v["file"]).write_bytes(v.pop("wav"))
                v.pop("audio", None)
            results.append({"name": name, "say": say, "sentence": sentence, "variants": variants,
                            "today": pronounce_review.caps_choice(name) if caps else ""})
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", help="a download of /admin's list (Download list)")
    ap.add_argument("--names", help="comma-separated names to try, instead of picking; "
                    "Name=respelling tries that respelling")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--no-sentence", action="store_true",
                    help="bare names only (half the calls)")
    ap.add_argument("--caps", nargs="?", const=CAPS_DEFAULT,
                    help="say words in capitals five ways instead (comma-separated; "
                    f"default {CAPS_DEFAULT})")
    ap.add_argument("--out", default=f"stress-trial/{time.strftime('%Y%m%d-%H%M')}")
    args = ap.parse_args()

    if args.caps:
        import hard_words

        hard_words.warm()       # so "today" is what a warm server hands the voice
        words = [w.strip().upper() for w in args.caps.split(",")
                 if re.fullmatch(r"\s*[A-Za-z]{2,12}\s*", w)]
        if not words:
            print("No words to try: give --caps letters only, comma-separated.")
            return 1
        return _finish(pathlib.Path(args.out),
                       [(w, "") for w in words[:args.limit]], not args.no_sentence, caps=True)

    if args.csv:
        rows = _from_csv(args.csv)
    else:
        rows = [(n, v[0]) for n, v in pronunciation.lexicon().entries().items()]
    given = [p.split("=", 1) for p in (args.names or "").split(",") if "=" in p]
    if given:
        # "Name=respelling": tried as given, with or without a list.
        typed = []
        for n, s in given:
            pair = pronunciation.clean(n, s)
            if pair:
                typed.append(pair)
            else:
                print(f"not a respelling the voice can use, skipped: {n.strip()}={s.strip()}")
        rows = typed + rows
    if args.names:
        wanted = {n.split("=", 1)[0].strip() for n in args.names.split(",") if n.strip()}
        seen: set = set()
        names = [(n, s) for n, s in rows
                 if n in wanted and not (n in seen or seen.add(n))][:args.limit]
        missing = wanted - {n for n, _ in names}
        if missing:
            print(f"not on the list: {', '.join(sorted(missing))}")
    else:
        names = pick(rows, args.limit)
    if not names:
        print("No names to try: " + (
            "none of --names is on the list - give them as Name=respelling."
            if args.names else
            "none on the list marks a stressed syllable in capitals."))
        return 1

    return _finish(pathlib.Path(args.out), names, not args.no_sentence)


def _finish(out: pathlib.Path, names: list, sentences: bool, caps: bool = False) -> int:
    import voice_check
    try:
        results = asyncio.run(run(names, out, sentences, caps=caps))
    except voice_check.VoiceCheckUnavailable as exc:
        print(f"No voice here: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - the voice's own words, not a traceback
        print(f"The voice could not be heard: {type(exc).__name__}: {exc}")
        print("Check the worker answers: python tools/probe_remote_voice.py")
        return 2
    (out / "index.html").write_text(_page(results, caps=caps), encoding="utf-8")
    with open(out / "results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "say", "sentence", "variant", "sent", "heard_say", "score", "file"])
        for r in results:
            for v in r["variants"]:
                w.writerow([r["name"], r["say"], r["sentence"], v["variant"], v["sent"],
                            v["heard_say"], v["score"], v["file"]])
    print(f"\nListen: {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
