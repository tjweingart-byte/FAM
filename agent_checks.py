"""Keith's checks that each agent keeps to its rule book (§491).

keith.md "always" 7: "Check that every agent is working within its rule book
and its own job ... Report any agent that breaks its rules, and ask
Netenyahu to hold it. Count every sentence Mclovin let play unchecked because
its check ran out of time, and tell the owner when that count rises." The
build prompt's list, each written from the agent's signed rule book and run
on a sample of what the agent left behind:

* **Sonic never wrote** (sonic.md never 1): he reads the archive through his
  own read-only user. Checked where it can be: while he is on Atlas, his
  connection must be a different database user from the archive's writer -
  the same user would let him write. A breach is urgent and names him, so
  `hold_agent` (§490) applies.
* **Consuela's deletions are within grace** (every-agent.md always 8,
  consuela.md always 11: marked, kept 30 days, then deleted): a deletion
  cannot be sampled after it happens, so what is checked is what decides
  it - her purge keeps `HOUSEKEEPER_SUPERSEDED_DAYS` - which must be at least
  `GRACE_DAYS`.
* **Mclovin's verdicts are among his named failures** (mclovin.md): every
  verdict he kept in the last day (`mclovin_flags`) names one of his checks
  and one of his results.
* **C3P0's titles carry no results** (c3p0.md; [title-from-content]): while
  his titles are on (`CONTEXTUALIZER_TITLES`), the episode's title is his
  (§375); each title he gives is kept as a step, `clean` or `result` by his
  own `has_result`, never the words (§495). His inputs are not kept
  anywhere to sample; his code refuses them before they leave (§370).
* **Nando's nodes exist** (nando.md): a page of his filed episodes
  names nodes the category tree holds: 200 filed episodes an hour, by key,
  coming round again (the nightly tag check, §405, reads them all at night);
  a breach stands until a whole pass is clean (§495).

And what Phase 3 left Keith to watch:

* **Mclovin's unchecked sentences** (decided item 32): the share of his
  sentences a check let play unchecked today, against the last week's; a
  rise is an incident.
* **Shakespeare's tandem outcomes** (§456): each episode's outcome is now kept
  as a measured step (`editor tandem`); a rise in `late` or `second_failed`
  is an incident. Watching only - nothing is spent.
* **The two weeks' progress** (`EDITOR_TRIAL`, §454): episodes and listens
  per arm, for /admin, so the owner knows when there is enough to read.

Reads only; never raises. Run within Keith's incidents round, at most hourly.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional
from urllib.parse import urlsplit

log = logging.getLogger("agent_checks")

#: The grace a removal is held for (every-agent.md always 8; consuela.md).
GRACE_DAYS = 30
#: How often the checks run; between, the last findings stand.
EVERY_SECONDS = 3600
#: Newest episodes sampled for C3P0's titles and Nando's nodes.
SAMPLE = 200
#: Mclovin's results that are verdicts (a pass is not kept).
VERDICTS = frozenset(("fail", "unsure", "unchecked"))
#: Sentences in a day before an unchecked share is read at all.
MIN_SENTENCES = 50
#: Outcomes of Shakespeare's tandem watched for a rise.
TANDEM_WATCHED = ("late", "second_failed")
#: Tandem episodes in a day before a share is read.
MIN_TANDEM = 20

_LAST: dict = {"at": 0.0, "findings": [], "read": True}


def _settings():
    from config import settings

    return settings


def _breach(role: str, check: str, title: str, problem: str, *,
            plan: Optional[dict] = None, **detail) -> dict:
    """An agent broke a rule. `plan` replaces any of the generic plan's
    fields (`heard`, `recommended`, `cost_risk`, `needs`) where the check
    knows better than "hold the agent" (§556)."""
    import incidents as INC

    said = {"heard": "Nothing yet: the episode was made as it is, and the agent's "
                     "rule book is what keeps it so.",
            "recommended": "Hold the agent (Keith's hold_agent asks Netenyahu, once "
                           "switched on) and look at what it did.",
            "cost_risk": "A held agent's work is skipped; FAM carries on without it.",
            "needs": "Your look at the agent; releasing a hold is yours.",
            **(plan or {})}
    return INC.finding(
        f"breach:{role}:{check}", "agents", INC.URGENT, title,
        detail={"role": role, "check": check, **detail},
        problem=problem, **said)


# ------------------------------------------------------------------ the checks
def _user(uri: str) -> str:
    try:
        return urlsplit(uri).username or ""
    except ValueError:
        return ""


def sonic_never_writes() -> list:
    s = _settings()
    if not (s.agents and getattr(s, "retriever", False)) or \
            (s.archive_backend or "").strip().lower() != "atlas":
        return []
    import credentials

    reader = credentials.active("MONGODB_READ_URI") or ""
    writer = credentials.active("MONGODB_URI") or ""
    if not reader or not writer:
        return []                     # without his user he does not run (§403)
    if reader == writer or (_user(reader) and _user(reader) == _user(writer)):
        return [_breach("retriever", "writer-user",
                        "Sonic connects as the archive's writer",
                        "MONGODB_READ_URI names the same database user as "
                        "MONGODB_URI, so Sonic could write (sonic.md never 1).")]
    return []


def consuela_keeps_grace() -> list:
    s = _settings()
    if not (s.agents and getattr(s, "housekeeper", False)):
        return []
    days = float(getattr(s, "housekeeper_superseded_days", GRACE_DAYS) or 0)
    if days < GRACE_DAYS:
        return [_breach("housekeeper", "grace",
                        f"Consuela deletes {days:g} days after marking, not {GRACE_DAYS}",
                        f"HOUSEKEEPER_SUPERSEDED_DAYS is {days:g}: a repeat she "
                        f"marks is deleted inside the {GRACE_DAYS}-day grace "
                        "(every-agent.md always 8).")]
    return []


def mclovin_names_his_failures(now: float) -> list:
    try:
        from agents import supervisor_checks, supervisor_training
        import db as store_db
        from paths import data_path

        if supervisor_training._STORE[0] is None and not store_db.has_tables(
                data_path("AGENTS_DB", "agents.db")):
            return []
        rows = supervisor_training.store()._rows(
            "SELECT check_name, result FROM mclovin_flags WHERE at >= ?", (now - 86400,))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read Mclovin's verdicts: %s", exc)
        return []
    named = set(supervisor_checks.CHECKS) | {"support"}
    odd = sorted({f"{r['check_name']}/{r['result']}" for r in rows
                  if r["check_name"] not in named or r["result"] not in VERDICTS})
    if odd:
        return [_breach("supervisor", "unnamed",
                        f"Mclovin gave {len(odd)} verdict(s) outside his named failures",
                        "Verdicts kept in the last day name a check or result "
                        f"not in his rule book: {', '.join(odd[:5])}.",
                        examples=odd[:10])]
    return []


def c3p0_titles_carry_no_results(now: float) -> list:
    """C3P0's own titles over the last day (§495): kept as a step when he
    gives one (`c3p0 title`: `clean` or `result`, never the words), so a
    writer's or an admin's title is never blamed on him and no listener's
    words reach an incident or an email."""
    s = _settings()
    if not (s.agents and getattr(s, "contextualizer", False)
            and getattr(s, "contextualizer_titles", False)):
        return []
    import pipeline_errors as PE

    if not PE.exists():
        return []
    PE.flush()
    # A failed read raises (§496): `findings` keeps the last answer, rather
    # than an unread check reading as clean and closing an open breach.
    rows = PE.store().steps_since(now - 86400, step="c3p0 title")
    stated = sum(1 for r in rows if r["outcome"] == "result")
    if stated:
        return [_breach("contextualizer", "title-result",
                        f"{stated} of C3P0's {len(rows)} titles today state a result",
                        "While his titles are on, the episode's title is his; "
                        f"{stated} he gave in the last day state an outcome, which "
                        "a title never does ([title-from-content]). The titles "
                        "themselves are not kept here: they come from listeners' "
                        "questions.", titles=len(rows), stated=stated)]
    return []


#: Nando's pages (§495): where the last page ended, the nodes seen so far in
#: this pass, and those of the last whole pass. A breach stands until a
#: whole pass is clean, so a clean page between two bad ones never closes
#: and reopens it.
#: Beside each pass's nodes, what was filed under each (§556): how many
#: episodes, the newest one's key and when it was written - so the alert can
#: say what it found, not only that it found something.
_NANDO: dict = {"after": "", "seen": set(), "last_pass": set(),
                "found": {}, "last_found": {}}

#: Filed under a missing node this recently means Nando is still doing it:
#: he names nodes the tree does not hold. Only older filings means the tree
#: changed under them (a prune, a rename, a re-seed) and Nando did nothing.
NANDO_STILL_FILING = 2 * 86400


def _nando_pass_ended() -> None:
    _NANDO.update(after="", last_pass=set(_NANDO["seen"]), seen=set(),
                  last_found=dict(_NANDO.get("found") or {}), found={})


def _nando_saw(rows) -> None:
    found = _NANDO.setdefault("found", {})
    for key, node, sourced, *_rest in rows:
        if not node:
            continue
        node = str(node)
        got = found.setdefault(node, {"episodes": 0, "newest": 0.0, "key": ""})
        got["episodes"] += 1
        if float(sourced or 0) >= got["newest"]:
            got["newest"], got["key"] = float(sourced or 0), str(key)


def _day(at: float) -> str:
    import datetime

    if not at:
        return "an unknown day"
    day = datetime.datetime.fromtimestamp(at)
    return f"{day:%b} {day.day}"


def _dead_node_breach(dead: list, cache, now: float) -> dict:
    """What Keith found, in words (§556): each missing node with its episodes,
    the newest's title and day; then which of two things it is, and the
    plan for that one - never "hold the agent" when the tree moved."""
    evidence = {n: (_NANDO.get("found") or {}).get(n)
                or (_NANDO.get("last_found") or {}).get(n)
                or {"episodes": 0, "newest": 0.0, "key": ""} for n in dead}
    total = sum(e["episodes"] for e in evidence.values())

    def title(key: str) -> str:
        try:
            return (cache.title(key) or "").strip() if key and hasattr(cache, "title") else ""
        except Exception:  # noqa: BLE001 - a title is colour, never the finding
            return ""

    lines = []
    for node in dead[:5]:
        e = evidence[node]
        said = title(e["key"])
        lines.append(f"\"{node}\" ({e['episodes']} episode{'s' if e['episodes'] != 1 else ''}, "
                     f"newest written {_day(e['newest'])}"
                     + (f": \"{said[:70]}\"" if said else "") + ")")
    more = f", and {len(dead) - 5} more" if len(dead) > 5 else ""
    newest = max((e["newest"] for e in evidence.values()), default=0.0)
    still = bool(newest) and now - newest < NANDO_STILL_FILING
    if still:
        why = (f"Nando filed one of these on {_day(newest)}: he is naming nodes the "
               "tree does not hold, which his rule book forbids (\"only nodes that exist\").")
        plan = {"recommended": "Hold Nando (Keith's hold_agent asks Netenyahu, once switched on) "
                               "and read the newest episode's filing on the Categories page: "
                               "which step chose the node, and where it came from.",
                "needs": "Your look at Nando's filing; releasing a hold is yours."}
    else:
        try:
            import history_archive as ha

            consuela = ha.consuela_runs()
        except Exception:  # noqa: BLE001
            consuela = False
        dry = bool(getattr(_settings(), "housekeeper_dry_run", True))
        why = (f"Nothing has been filed under them since {_day(newest)}: the tree changed "
               "under these episodes (a node pruned, renamed or re-seeded). Nando did "
               "nothing wrong, and holding him would not help.")
        if not consuela:
            state = ("Consuela is not running (she needs AGENTS, HOUSEKEEPER and her "
                     "rule book signed), so nothing moves them yet.")
        elif dry:
            state = "Consuela runs in dry run, so she records the move and makes none."
        else:
            state = "Consuela re-files them when her nightly task for each node runs."
        plan = {"recommended": "Re-file the episodes onto a node the tree holds: Consuela's "
                               "re-file (housekeeper:refile:<node>) moves them. " + state
                               + " Or put the node back in category_seed.py if it was "
                               "dropped by mistake.",
                "needs": "Whether these subjects should be nodes again, or move."}
    problem = ("Episodes are filed under nodes the tree does not hold: " + "; ".join(lines)
               + more + ". " + why)
    plan["heard"] = ("Little: an episode whose node is gone is read by the writer's own "
                     "category words instead (§371), so its picture and ranking fall back "
                     "to those. It is not counted under the node it was filed at.")
    plan["cost_risk"] = ("None from leaving it a while; the episodes still play. A re-file "
                         "rewrites their category, never their audio or script.")
    return _breach("categorizer", "dead-node",
                   f"{len(dead)} node(s) Nando filed {total} episode(s) under are not in the tree",
                   problem, plan=plan, examples=dead[:10],
                   episodes={n: evidence[n]["episodes"] for n in dead[:10]},
                   still_filing=still)


def nando_nodes_exist(cache, now: Optional[float] = None) -> list:
    s = _settings()
    if cache is None or not (s.agents and getattr(s, "categorizer", False)) \
            or not hasattr(cache, "tagged"):
        return []
    import categories
    import topics

    # A failed read, or no tree to read against, raises (§496): `findings`
    # keeps the last answer rather than closing a breach it did not look at.
    tree = topics.category_tree()
    if tree is None:
        raise LookupError("no category tree to check Nando's nodes against")
    rows = cache.tagged(after=_NANDO["after"], limit=SAMPLE)
    if not rows and _NANDO["after"]:
        _nando_pass_ended()
        rows = cache.tagged(after="", limit=SAMPLE)
    if not rows:
        return []
    _NANDO["seen"] |= {str(node) for _key, node, *_rest in rows if node}
    _nando_saw(rows)
    _NANDO["after"] = rows[-1][0]
    if len(rows) < SAMPLE:
        _nando_pass_ended()
    # Every node seen in this pass and the last, against today's tree: a node
    # put back closes the breach on the next round. A facet is the tree's
    # root, not a row in it (`categories.mint`), and Nando's last step files
    # an episode there ("broader, never wrong"): never a dead node. Counting
    # facets raised a false URGENT for `science` and `world` (11.12).
    dead = sorted(n for n in _NANDO["seen"] | _NANDO["last_pass"]
                  if not categories.holds(tree, n))
    if dead:
        return [_dead_node_breach(dead, cache, now or time.time())]
    return []


# --------------------------------------------------------- what Phase 3 left
def mclovin_unchecked(now: float) -> tuple[list, dict]:
    """The share of Mclovin's sentences left unchecked today against the
    last week's (decided item 32)."""
    import incidents as INC

    try:
        from agents import supervisor_training
        import db as store_db
        from paths import data_path

        if supervisor_training._STORE[0] is None and not store_db.has_tables(
                data_path("AGENTS_DB", "agents.db")):
            return [], {}
        store = supervisor_training.store()
        day, week = now - 86400, now - 8 * 86400

        def share(since: float, until: float) -> tuple:
            sentences = store._rows(
                "SELECT COALESCE(SUM(sentences), 0) AS n FROM mclovin_episodes "
                "WHERE at >= ? AND at < ?", (since, until))[0]["n"] or 0
            unchecked = store._rows(
                "SELECT COUNT(*) AS n FROM mclovin_flags WHERE result = 'unchecked' "
                "AND at >= ? AND at < ?", (since, until))[0]["n"] or 0
            return int(unchecked), int(sentences)

        today, today_n = share(day, now + 1)
        before, before_n = share(week, day)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read Mclovin's unchecked sentences: %s", exc)
        return [], {}
    out = {"today": today, "today_sentences": today_n,
           "week_before": before, "week_before_sentences": before_n}
    if today_n < MIN_SENTENCES:
        return [], out
    rate = today / today_n
    base = (before / before_n) if before_n else 0.0
    out.update(rate=round(rate, 4), base=round(base, 4))
    if today and rate > max(2 * base, base + 0.01):
        return [INC.finding(
            "watch:supervisor:unchecked", "agents", INC.WARN,
            f"Mclovin left {rate:.1%} of sentences unchecked today "
            f"(the week before: {base:.1%})",
            detail=out,
            problem=f"{today} of {today_n} sentences played unchecked because a "
                    "check ran out of time (decided item 32).",
            heard="Those sentences reached listeners without his checks.",
            recommended="Look at his time limit's misses (SUPERVISOR_TIMEOUT_"
                        "SECONDS, 2 s) and the model's latency today.",
            needs="Whether to raise his limit - a setting only you change.")], out
    return [], out


def tandem_outcomes(now: float) -> tuple[list, dict]:
    """Shakespeare's tandem outcomes over the last day against the week before
    (§456): a rise in `late` or `second_failed`."""
    import incidents as INC
    import pipeline_errors as PE

    try:
        if not PE.exists():
            return [], {}
        PE.flush()
        rows = PE.store().steps_since(now - 8 * 86400, step="editor tandem")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read Shakespeare's tandem: %s", exc)
        return [], {}

    def counts(since, until):
        out: dict = {}
        for r in rows:
            if since <= r["at"] < until:
                out[r["outcome"]] = out.get(r["outcome"], 0) + 1
        return out

    today, before = counts(now - 86400, now + 1), counts(now - 8 * 86400, now - 86400)
    detail = {"today": today, "week_before": before}
    n_today, n_before = sum(today.values()), sum(before.values())
    findings = []
    if n_today >= MIN_TANDEM:
        for outcome in TANDEM_WATCHED:
            rate = today.get(outcome, 0) / n_today
            base = before.get(outcome, 0) / n_before if n_before else 0.0
            if today.get(outcome) and rate > max(2 * base, base + 0.02):
                findings.append(INC.finding(
                    f"watch:editor:{outcome}", "agents", INC.WARN,
                    f"Shakespeare's tandem '{outcome}' on {rate:.0%} of episodes "
                    f"today (the week before: {base:.0%})",
                    detail=detail,
                    problem=f"{today[outcome]} of {n_today} episodes ended the "
                            f"tandem as '{outcome}' (§456).",
                    heard="Those episodes were written by the writer alone - as "
                          "before him; nothing is lost but his plan.",
                    recommended="Look at the editor's latency and errors today; "
                                "EDITOR_TANDEM_WAIT_SECONDS is the wait.",
                    needs="Nothing unless it persists. Watching only - nothing "
                          "was measured or spent for this."))
    return findings, detail


def trial_progress() -> dict:
    """The two weeks with and without Shakespeare (§454): episodes and listens
    per arm, so the owner knows when there is enough to read."""
    try:
        import episode_quality

        trial = episode_quality.editor_trial()
        return {arm: {"episodes": v.get("episodes"), "listens": v.get("listens")}
                for arm, v in (trial.get("arms") or {}).items()}
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read the trial: %s", exc)
        return {}


# ------------------------------------------------------------------ the round
CACHE: list = [None]


def register(cache) -> None:
    CACHE[0] = cache


def findings(now: Optional[float] = None, force: bool = False) -> list:
    """Every breach and every rise, at most once per `EVERY_SECONDS`; between,
    the last findings stand (so an open breach is not closed by a round that
    did not look). Never raises."""
    now = time.time() if now is None else now
    if not force and now - _LAST["at"] < EVERY_SECONDS:
        return list(_LAST["findings"])
    out: list = []
    before = _LAST.setdefault("by_check", {})
    for name, check in (("sonic", sonic_never_writes), ("consuela", consuela_keeps_grace),
                        ("mclovin", lambda: mclovin_names_his_failures(now)),
                        ("c3p0", lambda: c3p0_titles_carry_no_results(now)),
                        ("nando", lambda: nando_nodes_exist(CACHE[0])),
                        ("unchecked", lambda: mclovin_unchecked(now)[0]),
                        ("tandem", lambda: tandem_outcomes(now)[0])):
        try:
            got = list(check())
        except Exception as exc:  # noqa: BLE001 - one check never stops the rest
            # Not looked at is not clean (§496): its last findings stand, so
            # an open breach is neither closed nor reopened (and re-emailed).
            log.warning("agent check %s failed: %s", name, type(exc).__name__)
            got = list(before.get(name, []))
        before[name] = got
        out.extend(got)
    _LAST.update(at=now, findings=out)
    return out


def report(now: Optional[float] = None) -> dict:
    """For /admin: the watches' figures and the trial's progress."""
    now = time.time() if now is None else now
    return {"mclovin_unchecked": mclovin_unchecked(now)[1],
            "tandem": tandem_outcomes(now)[1],
            "trial": trial_progress(),
            "last_checked": _LAST["at"] or None,
            "open_findings": [f["cause"] for f in _LAST["findings"]]}


def reset() -> None:
    _LAST.update(at=0.0, findings=[], by_check={})
    CACHE[0] = None
    _NANDO.update(after="", seen=set(), last_pass=set())
