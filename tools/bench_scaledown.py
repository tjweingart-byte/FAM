#!/usr/bin/env python3
"""ScaleDown against our Haiku brief, on the three jobs ScaleDown offers.

    python tools/bench_scaledown.py                    # both sides, every job
    python tools/bench_scaledown.py --only scaledown   # no ANTHROPIC_API_KEY needed
    python tools/bench_scaledown.py --only haiku
    python tools/bench_scaledown.py --job compress --repeat 5
    python tools/bench_scaledown.py --json out.json    # every answer, for reading

The question is whether any part of the brief (`episode_intelligence.
understand`) or the evidence cut (`evidence_digest.digest`) could be handed to
ScaleDown's hosted endpoints (`api.scaledown.xyz`, auth `x-api-key`) and come
back faster without coming back worse. Three jobs:

* **classify** - the request's `intent` (one of `INTENTS`) and `live_domain`
  (`live_facts.LIVE_DOMAINS` or none). ScaleDown: two `/classify` calls at
  once, one per label set, with the field guide's own definitions as rubrics.
  Haiku: the brief.
* **extract** - `subject`, `place`, `live_entity` and `outcome_dependent`.
  ScaleDown: one `/extract` call (`entities` maps a name to a description) -
  and, because `/extract` returns spans of the typed words rather than
  values, `outcome_dependent` is also tried as a yes/no `/classify`. Haiku:
  the same brief - it writes all six fields in one go, so its latency is the
  same number under both jobs.
* **compress** - a news evidence packet, cut to what the question needs.
  ScaleDown: `/compress/raw/` with the question as `prompt` and the packet as
  `context`. Haiku: `evidence_digest.digest`, the extractive cut (§270).
  Checked: every output sentence is a sentence of the input, verbatim; every
  figure the answer needs survives; no figure appears that the input lacks;
  and each kept source keeps its `Published:` line.

**The labels are hand-written** (`GOLD`, by the field guide's definitions).
A field marked `None` is one where the guide leaves room for two readings;
it is not scored for either side, rather than scored against one of them.

Latency is wall time per job from this machine, sequential (one request at a
time), reported as p50 and p95 by nearest rank. With about thirty queries the
p95 is the second-slowest - read it as a rough tail, not a measurement of one.

The ScaleDown side needs `SCALEDOWN_API_KEY`; the Haiku side needs
`ANTHROPIC_API_KEY` and is skipped, saying so, without it. Nothing here is
cached and no result is invented for a side that did not run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import credentials  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import evidence_digest  # noqa: E402
import live_facts  # noqa: E402

BASE = os.environ.get("SCALEDOWN_BASE_URL", "https://api.scaledown.xyz")

# --------------------------------------------------------------------------
# The gold set
# --------------------------------------------------------------------------
#: `subject`, `place` and `live_entity` are scored by required words (any one
#: of the alternatives, every word of it), because a right subject can be
#: worded many ways. `entity_names` is the strict reading the field guide asks
#: for: the full official names, as a scoreboard lists them.
#: `None` = the guide allows two readings; not scored.
GOLD = [
    dict(q="who won the Eagles Giants game last night", intent="recap", domain="sports",
         subject=["eagles giants"], place="", entity=["eagles", "giants"],
         entity_names=["Philadelphia Eagles", "New York Giants"], outcome=True),
    dict(q="49ers game last night", intent="recap", domain="sports",
         subject=["49ers"], place="", entity=["49ers"],
         entity_names=["San Francisco 49ers"], outcome=True),
    dict(q="final score of the Yankees game", intent="recap", domain="sports",
         subject=["yankees"], place="", entity=["yankees"],
         entity_names=["New York Yankees"], outcome=True),
    dict(q="who won the World Series", intent="recap", domain="sports",
         subject=["world series"], place="", entity=None, entity_names=None, outcome=True),
    dict(q="how did the S&P 500 close today", intent="recap", domain="markets",
         subject=["s&p 500", "s&p"], place="", entity=["s&p"],
         entity_names=["S&P 500"], outcome=True),
    dict(q="who won the Ohio governor election", intent="recap", domain="elections",
         subject=["ohio governor"], place="", entity=None, entity_names=None, outcome=True),
    dict(q="did the Supreme Court rule on the tariff case", intent="recap", domain="",
         subject=["supreme court tariff"], place="", entity=[], entity_names=[], outcome=True),
    dict(q="what happened at the Apple event", intent="recap", domain="",
         subject=["apple event"], place="", entity=["apple"],
         entity_names=["Apple", "Apple Inc."], outcome=False),
    dict(q="Lakers vs Celtics preview tonight", intent="preview", domain="sports",
         subject=["lakers celtics"], place="", entity=["lakers", "celtics"],
         entity_names=["Los Angeles Lakers", "Boston Celtics"], outcome=False),
    dict(q="what's at stake in the Chiefs Bills game Sunday", intent="preview", domain="sports",
         subject=["chiefs bills"], place="", entity=["chiefs", "bills"],
         entity_names=["Kansas City Chiefs", "Buffalo Bills"], outcome=False),
    dict(q="what to expect from Apple's earnings report on Thursday", intent="preview",
         domain="markets", subject=["apple earnings"], place="", entity=["apple"],
         entity_names=["Apple", "Apple Inc."], outcome=False),
    dict(q="why did Nvidia stock fall today", intent="causal", domain="markets",
         subject=["nvidia"], place="", entity=["nvidia"], entity_names=["NVIDIA"],
         outcome=False),
    dict(q="why did Boeing fire its CEO", intent="causal", domain="",
         subject=["boeing"], place="", entity=["boeing"], entity_names=["Boeing"],
         outcome=False),
    dict(q="why is Detroit's car industry shrinking", intent="causal", domain="",
         subject=["detroit car"], place="", entity=[], entity_names=[], outcome=False),
    dict(q="why did the Eagles lose", intent="causal", domain=None,
         subject=["eagles"], place="", entity=["eagles"],
         entity_names=["Philadelphia Eagles"], outcome=None),
    dict(q="how is Patrick Mahomes playing this season", intent="update", domain="sports",
         subject=["mahomes"], place="", entity=["mahomes"],
         entity_names=["Patrick Mahomes"], outcome=False),
    dict(q="where do the Dodgers stand in the standings", intent="update", domain="sports",
         subject=["dodgers"], place="", entity=["dodgers"],
         entity_names=["Los Angeles Dodgers"], outcome=False),
    dict(q="is Tesla stock up today", intent="update", domain="markets",
         subject=["tesla"], place="", entity=["tesla"], entity_names=["Tesla"],
         outcome=False),
    dict(q="who is ahead in the Senate race", intent="update", domain="elections",
         subject=["senate"], place="", entity=None, entity_names=None, outcome=False),
    dict(q="San Anselmo town council news", intent="update", domain="",
         subject=["san anselmo council"], place="san anselmo", entity=[],
         entity_names=[], outcome=False),
    dict(q="how does the electoral college work", intent="explainer", domain="",
         subject=["electoral college"], place="", entity=[], entity_names=[], outcome=False),
    dict(q="how do interest rates affect mortgages", intent="explainer", domain="",
         subject=["interest rate mortgage"], place="", entity=[], entity_names=[],
         outcome=False),
    dict(q="how does a VAR review work in soccer", intent="explainer", domain="",
         subject=["var"], place="", entity=[], entity_names=[], outcome=False),
    dict(q="iPhone 17 vs Pixel 10 cameras", intent="comparison", domain="",
         subject=["iphone pixel"], place="", entity=None, entity_names=None, outcome=False),
    dict(q="Roth IRA vs traditional IRA", intent="comparison", domain="",
         subject=["roth ira"], place="", entity=[], entity_names=[], outcome=False),
    dict(q="teach me how to read a balance sheet", intent="teaching", domain="",
         subject=["balance sheet"], place="", entity=[], entity_names=[], outcome=False),
    dict(q="teach me the offside rule", intent="teaching", domain="",
         subject=["offside"], place="", entity=[], entity_names=[], outcome=False),
    dict(q="tell me something interesting about octopuses", intent="discovery", domain="",
         subject=["octopus"], place="", entity=[], entity_names=[], outcome=False),
    dict(q="surprise me with something about Roman history", intent="discovery", domain="",
         subject=["roman"], place="", entity=[], entity_names=[], outcome=False),
    dict(q="weather in San Anselmo this weekend", intent=None, domain="weather",
         subject=["weather san anselmo"], place="san anselmo", entity=[],
         entity_names=[], outcome=False),
    dict(q="will it rain in Chicago tomorrow", intent=None, domain="weather",
         subject=["chicago"], place="chicago", entity=[], entity_names=[], outcome=False),
    dict(q="the history of Paris", intent=None, domain="",
         subject=["paris"], place="", entity=[], entity_names=[], outcome=False),
]

# --------------------------------------------------------------------------
# Rubrics: the field guide's own words, so both sides are asked the same thing
# --------------------------------------------------------------------------
INTENT_RUBRICS = {
    "recap": "tell me what happened, if it has happened (a result, a game, an event that took place)",
    "preview": "something is coming; tell me what to expect",
    "causal": "why did X do Y; why is something happening",
    "update": "where does X stand now; the current state of something ongoing",
    "explainer": "how does X work",
    "comparison": "X versus Y",
    "teaching": "teach me to do or think about X",
    "discovery": "tell me something about X, unprompted; surprise me",
}
assert set(INTENT_RUBRICS) == set(ei.INTENTS), "INTENTS changed; update the rubrics"

DOMAIN_RUBRICS = {
    "sports": "turns on a score, fixture or standing, or on a player's season, form or injury",
    "markets": "turns on a price, index or rate",
    "elections": "turns on a vote, a race, a referendum or the odds on one",
    "weather": "what they want is the weather or a forecast somewhere",
    "none": "none of these: the answer does not turn on a live score, price, vote or "
            "forecast (for example how something works, history, a company's decisions)",
}
assert set(DOMAIN_RUBRICS) - {"none"} == set(live_facts.LIVE_DOMAINS), \
    "LIVE_DOMAINS changed; update the rubrics"

OUTCOME_RUBRICS = {
    "yes": "they want a result that only exists once something concludes: a final score, "
           "a winner, a verdict, a closing price, a vote count",
    "no": "they want how something works, what someone is like, what is at stake, a "
          "preview, a current standing or an explanation",
}

#: `/extract` descriptions. `short` is a phrase per field, which is what an
#: entity extractor is built for; `guide` is the field guide's paragraph.
EXTRACT_SHORT = {
    "subject": "the entity or event the question is about",
    "place": "a town, city or county whose local news, events or weather is asked about",
    "live_entity": "a sports team, athlete, company, fund or stock index",
    "outcome_dependent": "true if the question asks for a final score, winner, verdict, "
                         "closing price or vote count; false otherwise",
}


def _guide_descriptions() -> dict:
    out = {}
    for line in ei.field_guide(list(EXTRACT_SHORT)).split("\n- "):
        m = re.match(r"-?\s*\*\*(\w+)\*\*\s*-\s*(.*)", line.strip(), re.S)
        if m:
            out[m.group(1)] = " ".join(m.group(2).split())
    return out


# --------------------------------------------------------------------------
# ScaleDown
# --------------------------------------------------------------------------
def _post(path: str, body: dict, timeout: float = 60.0) -> tuple:
    """`(seconds, json)`. Raises on HTTP errors, with the body in the message."""
    request = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"x-api-key": os.environ.get("SCALEDOWN_API_KEY", ""),
                 "content-type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{path} HTTP {exc.code}: {exc.read()[:300]!r}") from None
    return time.perf_counter() - started, data


def sd_classify(text: str, rubrics: dict) -> tuple:
    seconds, data = _post("/classify", {
        "text": text,
        "labels": [{"name": k, "rubric": v} for k, v in rubrics.items()]})
    return seconds, data.get("top_label", "")


def sd_extract(text: str, descriptions: dict) -> tuple:
    seconds, data = _post("/extract", {"text": text, "entities": descriptions})
    return seconds, data.get("entities") or []


def sd_fields(spans: list, threshold: float) -> dict:
    """`/extract` spans as brief fields. `subject` takes its most confident
    span; `place` and `live_entity` keep spans at or above `threshold`."""
    by: dict = {}
    for span in spans:
        by.setdefault(span.get("type"), []).append(span)
    for spans_ in by.values():
        spans_.sort(key=lambda s: -float(s.get("confidence") or 0))
    subject = (by.get("subject") or [{}])[0].get("text", "")
    place = next((s["text"] for s in by.get("place", [])
                  if float(s.get("confidence") or 0) >= threshold), "")
    names, seen = [], set()
    for s in by.get("live_entity", []):
        if float(s.get("confidence") or 0) >= threshold and s["text"].lower() not in seen:
            seen.add(s["text"].lower())
            names.append(s["text"])
    od = by.get("outcome_dependent", [])
    outcome = bool(od) and od[0].get("text", "").strip().lower() not in ("false", "no")
    return {"subject": subject, "place": place, "live_entity": ", ".join(names),
            "outcome_dependent": outcome, "spans": spans}


def run_scaledown(gold: list, descriptions: dict, threshold: float) -> list:
    rows = []
    pool = ThreadPoolExecutor(max_workers=3)
    for g in gold:
        q = g["q"]
        row = {"q": q}
        try:
            started = time.perf_counter()
            fi = pool.submit(sd_classify, q, INTENT_RUBRICS)
            fd = pool.submit(sd_classify, q, DOMAIN_RUBRICS)
            (si, intent), (sdm, domain) = fi.result(), fd.result()
            row["classify_s"] = time.perf_counter() - started
            row["classify_calls_s"] = [si, sdm]
            row["intent"] = intent
            row["domain"] = "" if domain == "none" else domain

            se, spans = sd_extract(q, descriptions)
            row["extract_s"] = se
            row.update(sd_fields(spans, threshold))

            so, od = sd_classify(q, OUTCOME_RUBRICS)
            row["outcome_classify_s"] = so
            row["outcome_by_classify"] = od == "yes"
        except Exception as exc:  # noqa: BLE001 - a failed row is reported, not hidden
            row["error"] = str(exc)
        rows.append(row)
        print(f"  scaledown  {q[:48]:48}  {row.get('intent', '!'):10} "
              f"{row.get('domain', '') or '-':9} {row.get('error', '')}", flush=True)
    return rows


# --------------------------------------------------------------------------
# Haiku: the real brief
# --------------------------------------------------------------------------
async def run_haiku(gold: list) -> list:
    rows = []
    for g in gold:
        q = g["q"]
        started = time.perf_counter()
        brief = await ei.understand(q, minutes=2)
        seconds = time.perf_counter() - started
        rows.append({
            "q": q, "brief_s": seconds, "degraded": bool(brief.degraded),
            "notes": list(brief.notes), "intent": brief.intent,
            "domain": brief.live_domain, "subject": brief.subject,
            "place": brief.place, "live_entity": brief.live_entity,
            "outcome_dependent": bool(brief.outcome_dependent),
        })
        print(f"  haiku      {q[:48]:48}  {brief.intent:10} "
              f"{brief.live_domain or '-':9} {seconds:5.2f}s"
              f"{'  DEGRADED' if brief.degraded else ''}", flush=True)
    return rows


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9&]+", " ", (text or "").lower()).split())


def _has_words(prediction: str, alternatives) -> bool:
    words = set(_norm(prediction).split())
    joined = _norm(prediction)
    for alt in alternatives:
        need = _norm(alt).split()
        # "octopus" matches "octopuses"; whole words otherwise.
        if all(w in words or any(x.startswith(w) for x in words) or w in joined
               for w in need):
            return True
    return False


def score(gold: list, rows: list, outcome_key: str = "outcome_dependent") -> dict:
    """Correct / scored for each field."""
    tally = {k: [0, 0] for k in ("intent", "domain", "subject", "place", "entity",
                                 "entity_names", "outcome")}
    misses: dict = {k: [] for k in tally}

    def mark(field_, ok, q, got):
        tally[field_][1] += 1
        tally[field_][0] += bool(ok)
        if not ok:
            misses[field_].append((q, got))

    for g, r in zip(gold, rows):
        if "error" in r:
            continue
        q = g["q"]
        if g["intent"] is not None:
            mark("intent", r["intent"] == g["intent"], q, r["intent"])
        if g["domain"] is not None:
            mark("domain", (r["domain"] or "") == g["domain"], q, r["domain"])
        mark("subject", _has_words(r["subject"], g["subject"]), q, r["subject"])
        if g["place"]:
            mark("place", _has_words(r["place"], [g["place"]]), q, r["place"])
        else:
            mark("place", not r["place"].strip(), q, r["place"])
        if g["entity"] is not None:
            if g["entity"]:
                ok = all(_has_words(r["live_entity"], [e]) for e in g["entity"])
            else:
                ok = not r["live_entity"].strip()
            mark("entity", ok, q, r["live_entity"])
        if g["entity_names"] is not None:
            got = {_norm(x) for x in r["live_entity"].split(",") if x.strip()}
            want = g["entity_names"]
            if not want:
                ok = not got
            elif len(want) == 2 and want[1].startswith(want[0]):   # alternatives
                ok = got in ({_norm(want[0])}, {_norm(want[1])})
            else:
                ok = got == {_norm(x) for x in want}
            mark("entity_names", ok, q, r["live_entity"])
        if g["outcome"] is not None:
            mark("outcome", r[outcome_key] == g["outcome"], q, r[outcome_key])
    return {"tally": tally, "misses": misses}


def pct(values: list, share: float):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    # Nearest rank.
    import math
    return values[max(0, math.ceil(share * len(values)) - 1)]


def _lat(values: list) -> str:
    p50, p95 = pct(values, 0.5), pct(values, 0.95)
    if p50 is None:
        return "-"
    return f"p50 {p50:.2f}s  p95 {p95:.2f}s  (n={len([v for v in values if v is not None])})"


def _acc(t: list) -> str:
    return f"{t[0]}/{t[1]} ({100 * t[0] / t[1]:.0f}%)" if t[1] else "-"


# --------------------------------------------------------------------------
# Compression
# --------------------------------------------------------------------------
#: Synthetic packets in `research.build_packet`'s shape: the same headers,
#: the same `Key evidence:` passages, with the padding a real search returns.
#: `needs` are the figures the answer turns on - losing one is a failure.
PACKETS = [
    dict(
        query="who won the Eagles Giants game last night",
        domain="sports",
        needs=["27-20", "254 yards", "2 touchdowns", "9-3", "1:52"],
        packet="""SOURCE 1
Title: Eagles hold off Giants 27-20 behind Hurts, late defensive stand
Published: 2026-10-21 (yesterday)
Source type: wire service
Key evidence:
The Philadelphia Eagles beat the New York Giants 27-20 on Tuesday night at Lincoln Financial Field. Jalen Hurts threw for 254 yards and 2 touchdowns and ran for another score. The Giants reached the Philadelphia 11-yard line with 1:52 left before a fourth-down pass fell incomplete.
The crowd of 69,796 was the largest of the season at the stadium. Fans began tailgating in the parking lots around noon despite temperatures in the 40s.

SOURCE 2
Title: Eagles improve to 9-3, keep pace in NFC East
Published: 2026-10-21 (yesterday)
Source type: national outlet
Key evidence:
The win moved Philadelphia to 9-3, one game ahead of Dallas in the NFC East. Saquon Barkley added 88 rushing yards on 19 carries. New York fell to 4-8 and has lost three straight.
Philadelphia's next game is at Washington on Sunday. The team has not lost back-to-back games since 2023.

SOURCE 3
Title: Giants coach defends fourth-down call
Published: 2026-10-21 (yesterday)
Source type: regional outlet
Key evidence:
Giants coach Brian Daboll said he would make the same fourth-down call again. "We had the right play, we just didn't execute it," Daboll said. Daboll is in the fourth year of his contract.
The Giants' rookie quarterback completed 21 of 34 passes for 231 yards and an interception.

SOURCE 4
Title: Where to watch, parking and tailgate guide for Eagles home games
Published: 2026-09-02 (7 weeks ago)
Source type: local blog
Key evidence:
Parking lots open five hours before kickoff and cost $60 per car. The stadium's bag policy allows clear bags up to 12 by 6 by 12 inches. Lincoln Financial Field opened in 2003 and seats about 69,000.
""",
    ),
    dict(
        query="how did Nvidia's earnings go",
        domain="markets",
        needs=["$57.0 billion", "62%", "$1.30", "$54.9 billion", "$65 billion", "4.2%"],
        packet="""SOURCE 1
Title: Nvidia tops estimates as data center sales jump 62%
Published: 2026-10-21 (yesterday)
Source type: wire service
Key evidence:
Nvidia reported third-quarter revenue of $57.0 billion, up 62% from a year earlier and above the $54.9 billion analysts had expected. Adjusted earnings were $1.30 per share. Data center revenue rose to $51.2 billion.
The company guided fourth-quarter revenue to about $65 billion, plus or minus 2%.

SOURCE 2
Title: Nvidia shares rise after the bell
Published: 2026-10-21 (yesterday)
Source type: national outlet
Key evidence:
Shares rose 4.2% in after-hours trading to $198.40 following the report. The stock had gained 38% this year before the results. Chief executive Jensen Huang said demand for its newest chips was "off the charts."
Huang wore his trademark black leather jacket on the call, as he has at every earnings event since 2019.

SOURCE 3
Title: What analysts are watching in the AI chip race
Published: 2026-10-15 (1 week ago)
Source type: trade publication
Key evidence:
Analysts expected gross margins near 73.5% for the quarter. Competition from custom chips built by cloud providers is the main long-term risk, several analysts said. AMD is due to report its own results on November 4.
The semiconductor index has risen 21% in 2026.

SOURCE 4
Title: A short history of Nvidia
Published: 2025-03-10 (1 year ago)
Source type: reference
Key evidence:
Nvidia was founded in 1993 by Jensen Huang, Chris Malachowsky and Curtis Priem. Its first major product was a graphics card for personal computers. The company is headquartered in Santa Clara, California.
""",
    ),
    dict(
        query="did the city council pass the budget",
        domain="",
        needs=["7-2", "$4.1 billion", "3.5%", "$120 million", "July 1"],
        packet="""SOURCE 1
Title: Council approves $4.1 billion budget after late amendment
Published: 2026-10-22 (today)
Source type: regional outlet
Key evidence:
The city council voted 7-2 on Wednesday to approve a $4.1 billion operating budget for the coming fiscal year. The budget raises property taxes by 3.5% and adds $120 million for road repairs. It takes effect July 1.
Council members Ruiz and Okafor voted against it, saying the tax increase was too steep.

SOURCE 2
Title: What the new budget means for residents
Published: 2026-10-22 (today)
Source type: local outlet
Key evidence:
A homeowner with a house assessed at $400,000 will pay about $168 more a year, according to the finance department. The budget also funds 45 new police officers and keeps library hours unchanged. A late amendment moved $8 million from the parking fund to transit.
The meeting lasted more than six hours and ended shortly after midnight.

SOURCE 3
Title: Mayor's budget proposal draws criticism
Published: 2026-09-30 (3 weeks ago)
Source type: regional outlet
Key evidence:
The mayor first proposed a 5% property tax increase in September. Several neighborhood groups organized against the plan at public hearings. The council's finance committee cut the increase to 3.5% in October.
City hall was built in 1911 and was renovated in 1998.
""",
    ),
]

_FIGURE = re.compile(
    r"\$\d[\d,]*(?:\.\d+)?(?:\s(?:billion|million|trillion))?|\d+:\d\d|\d+-\d+|"
    r"\d[\d,]*(?:\.\d+)?%?")
_HEADER = re.compile(r"^(SOURCE \d+|Title:|Published:|Source type:|Key evidence:)")


def _figures(text: str) -> list:
    return _FIGURE.findall(text)


def _sentences(text: str) -> list:
    """The non-header sentences of a packet, split as the digest splits them."""
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if line and not _HEADER.match(line):
            out += evidence_digest.split_sentences(line)
    return out


def _raw_sentences(text: str) -> list:
    body = " ".join(line.strip() for line in text.split("\n")
                    if line.strip() and not _HEADER.match(line.strip()))
    return [p.strip(" \"'“”") for p in
            evidence_digest._SENTENCE_BREAK.split(" ".join(body.split())) if p.strip()]


def check_compression(packet: str, needs: list, output: str) -> dict:
    """What a cut kept and whether it kept it honestly."""
    flat = " ".join(packet.split())
    sentences = _sentences(output)
    reworded = [s for s in sentences if " ".join(s.split()) not in flat]
    # In the packet, but not one of its sentences: cut off mid-sentence.
    # Split raw (no short-piece merging) and quote-blind, on both sides alike.
    whole = set(_raw_sentences(packet))
    truncated = [s for s in _raw_sentences(output)
                 if s not in whole and " ".join(s.split()) in flat]
    # Every kept sentence under its own source's header, or its date and
    # kind of source no longer reach the writer with it.
    home = {}
    for block in re.split(r"(?m)^(?=SOURCE \d+\s*$)", packet):
        if block.startswith("SOURCE"):
            for s in _sentences(block):
                home[s] = block.split("\n", 1)[0].strip()
    misfiled, current = 0, None
    for line in output.split("\n"):
        line = line.strip()
        if re.match(r"^SOURCE \d+$", line):
            current = line
        elif line and not _HEADER.match(line):
            misfiled += sum(home.get(s, current) != current
                            for s in evidence_digest.split_sentences(line))
    seen, duplicated = set(), 0
    for s in sentences:
        duplicated += s in seen
        seen.add(s)
    have = set(_figures(output))
    invented = sorted(have - set(_figures(packet)))
    lost_needed = [n for n in needs if n not in output]
    all_in = set(_figures(" ".join(_sentences(packet))))
    lost_any = sorted(all_in - have)
    # A kept source keeps its date: every `SOURCE n` whose sentences appear
    # must still be followed by its `Published:` line.
    sources = re.split(r"(?m)^(?=SOURCE \d+\s*$)", packet)
    dated, kept_sources = 0, 0
    for block in sources:
        if not block.startswith("SOURCE"):
            continue
        body = _sentences(block)
        if any(" ".join(s.split()) in " ".join(output.split()) for s in body):
            kept_sources += 1
            pub = re.search(r"^Published: .*$", block, re.M)
            dated += bool(pub and pub.group(0) in output)
    in_chars = len(" ".join(_sentences(packet)))
    out_chars = len(" ".join(sentences))
    return {
        "sentences_out": len(sentences), "reworded": reworded,
        "truncated": truncated, "misfiled": misfiled,
        "duplicated": duplicated, "invented_figures": invented,
        "lost_needed": lost_needed, "lost_any_figures": lost_any,
        "figures_kept": f"{len(all_in & have)}/{len(all_in)}",
        "kept_sources": kept_sources, "dated_sources": dated,
        "passage_chars": f"{out_chars}/{in_chars}",
        "ratio": round(out_chars / in_chars, 2) if in_chars else None,
    }


def run_compress_scaledown(repeat: int, rate) -> list:
    rows = []
    for p in PACKETS:
        for i in range(repeat):
            body = {"prompt": p["query"], "context": p["packet"]}
            if rate is not None:
                body["scaledown"] = {"rate": rate}
            try:
                seconds, data = _post("/compress/raw/", body)
                res = data.get("results") or {}
                out = res.get("compressed_prompt", "")
                row = {"query": p["query"], "s": seconds, "server_ms": data.get("latency_ms"),
                       "output": out, **check_compression(p["packet"], p["needs"], out)}
            except Exception as exc:  # noqa: BLE001
                row = {"query": p["query"], "error": str(exc)}
            rows.append(row)
            print(f"  scaledown  compress  {p['query'][:40]:40} "
                  f"{row.get('s', 0):5.2f}s ratio={row.get('ratio')} "
                  f"lost_needed={row.get('lost_needed')} {row.get('error', '')}", flush=True)
    return rows


async def run_compress_haiku(repeat: int) -> list:
    rows = []
    for p in PACKETS:
        for i in range(repeat):
            plan = SimpleNamespace(
                query=p["query"], evidence=p["packet"], live=None,
                brief=SimpleNamespace(must_establish=[], live_domain=p["domain"],
                                      live_route=""))
            started = time.perf_counter()
            result = await evidence_digest.digest(plan)
            seconds = time.perf_counter() - started
            # "" means the writer reads the packet as it was.
            out = result.text or p["packet"]
            rows.append({"query": p["query"], "s": seconds, "outcome": result.outcome,
                         "detail": result.detail, "output": out,
                         **check_compression(p["packet"], p["needs"], out)})
            print(f"  haiku      compress  {p['query'][:40]:40} {seconds:5.2f}s "
                  f"{result.outcome} ratio={rows[-1]['ratio']} "
                  f"lost_needed={rows[-1]['lost_needed']}", flush=True)
    return rows


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def report_fields(name: str, gold, rows, lat_classify, lat_extract, extra=None):
    s = score(gold, rows)
    t = s["tally"]
    print(f"\n### {name}")
    print(f"classify  latency  {_lat(lat_classify)}")
    print(f"          intent {_acc(t['intent'])}   live_domain {_acc(t['domain'])}")
    print(f"extract   latency  {_lat(lat_extract)}")
    print(f"          subject {_acc(t['subject'])}   place {_acc(t['place'])}   "
          f"live_entity {_acc(t['entity'])} (official names {_acc(t['entity_names'])})   "
          f"outcome_dependent {_acc(t['outcome'])}")
    if extra:
        print(extra)
    for field_, misses in s["misses"].items():
        for q, got in misses:
            print(f"    miss {field_:12} {q!r:58} -> {got!r}")
    return s


def report_compress(name: str, rows: list):
    ok = [r for r in rows if "error" not in r]
    print(f"\n### {name} - compress")
    print(f"latency  {_lat([r['s'] for r in ok])}   errors {len(rows) - len(ok)}")
    if not ok:
        return
    reworded = sum(len(r["reworded"]) for r in ok)
    invented = sum(len(r["invented_figures"]) for r in ok)
    lost = sum(len(r["lost_needed"]) for r in ok)
    needed = sum(len(p["needs"]) for p in PACKETS) * (len(ok) // len(PACKETS) or 1)
    dup = sum(r["duplicated"] for r in ok)
    undated = sum(r["kept_sources"] - r["dated_sources"] for r in ok)
    ratio = sorted(r["ratio"] for r in ok if r["ratio"] is not None)
    truncated = sum(len(r["truncated"]) for r in ok)
    misfiled = sum(r["misfiled"] for r in ok)
    print(f"truncated sentences {truncated}   sentences under another source's "
          f"header {misfiled}")
    print(f"reworded sentences {reworded}   invented figures {invented}   "
          f"needed figures lost {lost}/{needed}   duplicated sentences {dup}   "
          f"kept sources without their date {undated}")
    print(f"kept / original passage length  median {pct(ratio, 0.5)}")
    for r in ok:
        print(f"    {r['query'][:40]:40} ratio={r['ratio']} figures={r['figures_kept']} "
              f"lost_needed={r['lost_needed']} reworded={len(r['reworded'])} "
              f"dup={r['duplicated']} dated={r['dated_sources']}/{r['kept_sources']}"
              + (f" [{r['outcome']}]" if "outcome" in r else ""))
        for s in r["reworded"][:3]:
            print(f"        reworded: {s[:110]!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", choices=("scaledown", "haiku"))
    ap.add_argument("--job", choices=("fields", "compress"))
    ap.add_argument("--repeat", type=int, default=3, help="runs per packet")
    ap.add_argument("--descriptions", choices=("short", "guide"), default="short")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="/extract confidence a place or live_entity span needs")
    ap.add_argument("--rate", default=None,
                    help='/compress/raw/ scaledown.rate ("auto" or a number)')
    ap.add_argument("--json", help="write every answer here")
    args = ap.parse_args()

    out: dict = {}
    sides = [args.only] if args.only else ["scaledown", "haiku"]
    if "scaledown" in sides and not os.environ.get("SCALEDOWN_API_KEY"):
        print("scaledown: skipped - SCALEDOWN_API_KEY is not set")
        sides.remove("scaledown")
    if "haiku" in sides and not credentials.active("ANTHROPIC_API_KEY"):
        print("haiku: skipped - no ANTHROPIC_API_KEY here, so there is no Haiku "
              "number to report. Run this where the key is.")
        sides.remove("haiku")
    rate = args.rate
    if rate not in (None, "auto"):
        rate = float(rate)

    if args.job in (None, "fields"):
        if "scaledown" in sides:
            desc = EXTRACT_SHORT if args.descriptions == "short" else _guide_descriptions()
            print(f"\nScaleDown fields ({len(GOLD)} queries, {args.descriptions} descriptions)")
            out["scaledown_fields"] = run_scaledown(GOLD, desc, args.threshold)
        if "haiku" in sides:
            print(f"\nHaiku brief ({len(GOLD)} queries)")
            out["haiku_fields"] = asyncio.run(run_haiku(GOLD))
    if args.job in (None, "compress"):
        if "scaledown" in sides:
            print(f"\nScaleDown compress ({len(PACKETS)} packets x {args.repeat})")
            out["scaledown_compress"] = run_compress_scaledown(args.repeat, rate)
        if "haiku" in sides:
            print(f"\nHaiku evidence cut ({len(PACKETS)} packets x {args.repeat})")
            out["haiku_compress"] = asyncio.run(run_compress_haiku(args.repeat))

    print("\n" + "=" * 78)
    if "scaledown_fields" in out:
        rows = out["scaledown_fields"]
        ok = [r for r in rows if "error" not in r]
        alt = score(GOLD, [r if "error" in r else {**r, "outcome_dependent": r["outcome_by_classify"]}
                           for r in rows])["tally"]["outcome"]
        report_fields(
            "ScaleDown", GOLD, rows,
            [r["classify_s"] for r in ok], [r["extract_s"] for r in ok],
            extra=(f"          outcome_dependent as a yes/no /classify instead: {_acc(alt)}, "
                   f"latency {_lat([r['outcome_classify_s'] for r in ok])}\n"
                   f"          errors {len(rows) - len(ok)}"))
    if "haiku_fields" in out:
        rows = out["haiku_fields"]
        brief = [r["brief_s"] for r in rows]
        report_fields("Haiku brief (one brief answers both jobs)", GOLD, rows, brief, brief,
                      extra=f"          degraded briefs {sum(r['degraded'] for r in rows)}")
    if "scaledown_compress" in out:
        report_compress("ScaleDown", out["scaledown_compress"])
    if "haiku_compress" in out:
        report_compress("Haiku evidence cut", out["haiku_compress"])
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1, default=str)
        print(f"\nevery answer: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
