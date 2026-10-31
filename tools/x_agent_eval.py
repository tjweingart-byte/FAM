"""The X agent against real posts, side by side, before it is trusted (§366).

The guardrails brief (FAM_X_Marketing_Agent_Guardrails.pdf §10): "Before
deployment, compare its proposed output against 10 real posts using
side-by-side original post / episode / comment reviews." This runs the same
read, gates and drafting `/admin/x` uses - against the kept episodes of this
machine's cache - over posts a person supplies, or ones X's search finds, and
writes one HTML page: each post beside FAM's central message, opportunity,
relevance (with why), recommendation, episode (kept, proposed or none) and
comment, with an empty column for the reviewer's verdict.

Nothing is stored and nothing is posted. Posts a person supplies cost no X
read; `--from-x` searches the way a run does, inside the same ceilings'
spirit (one search per seed, capped by `--max-reads`).

    python tools/x_agent_eval.py --posts posts.json --out x_agent_eval.html
    python tools/x_agent_eval.py --from-x 10 --out x_agent_eval.html

`posts.json` is a list of {"url": "https://x.com/<handle>/status/<id>",
"text": "...", "replies": ["...", ...]} - `replies` optional, read as
context only. Without ANTHROPIC_API_KEY every card is read in code and says
so: the page is then a layout check, never a judgement of the writing.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import outreach_intelligence as OI  # noqa: E402
import x_threads as X  # noqa: E402

_STATUS = re.compile(r"x\.com/([A-Za-z0-9_]{1,15})/status/(\d+)|twitter\.com/([A-Za-z0-9_]{1,15})/status/(\d+)")


def post_from(row: dict, n: int) -> X.Post:
    """A supplied post as the agent reads one. The URL gives the author and
    id; counts default to a post the screen would pass, since a person chose it."""
    m = _STATUS.search(row.get("url") or "")
    author = (m.group(1) or m.group(3)) if m else (row.get("author") or "someone")
    pid = (m.group(2) or m.group(4)) if m else str(row.get("id") or n)
    return X.Post(id=pid, text=str(row.get("text") or ""), created_at=time.time() - 3600,
                  author=author.lstrip("@"), author_name=row.get("author_name") or author,
                  followers=int(row.get("followers") or 5000),
                  likes=int(row.get("likes") or 50), replies=len(row.get("replies") or []),
                  lang="en")


def candidate(post: X.Post, episodes: list, replies: list) -> X.Candidate:
    """The card a run would build for this post: its best on-subject kept
    episodes, or none (then a comment, or a proposal)."""
    ranked = [e for e in OI.rank(episodes, post.text, limit=OI.OFFERED + 1)
              if e["names"] and not e["off_subject"]]
    options = [X._seed_of(e) for e in ranked][:OI.OFFERED]
    seed = options[0] if options else None
    parts = {"match": ranked[0]["match"] if ranked else 0.0}
    kind = X.QUESTION_ONLY if not seed or parts["match"] < X.LINK_FLOOR else (
        X.THREAD_TO_EPISODE if X.asks(post.text) else X.ANGLE_AND_EPISODE)
    cand = X.Candidate(post, seed, 0.0, parts, kind, "")
    cand.options = [(o, e["match"]) for o, e in zip(options, ranked)]
    cand.replies = replies
    return cand


async def judge(cache, cand: X.Candidate) -> dict:
    cand.material = {s.key: OI.material(cache, s.key) for s in X.offered(cand)}
    reply, by, dollars, verdict = await X.judge_and_draft(cand)
    dropped = X.apply_verdict(cand, verdict)
    a = cand.assess or {}
    return {"post": cand.post, "replies": cand.replies, "dropped": dropped,
            "reply": "" if dropped else reply, "by": by, "dollars": dollars,
            "relevance": a.get("relevance"), "why": a.get("relevance_why", ""),
            "author_test": a.get("author_test", True), "central": a.get("central", ""),
            "angle": (cand.read or {}).get("angle", ""),
            "recommendation": ("skip" if dropped else a.get("recommendation", "")),
            "episode": (cand.seed if cand.kind != X.QUESTION_ONLY and not dropped else None),
            "proposed": a.get("proposed"),
            "anchored": X.anchored(reply, cand.post.text) if reply and not dropped else None,
            "promo": X.promotional(reply) if reply and not dropped else []}


async def from_x(cache, n: int, max_reads: int) -> list:
    """`n` posts X's search finds for the kept episodes, screened the way a
    run screens them - one per author, no reply, no grief, no flame war."""
    ok, why = X.available()
    if not ok:
        raise SystemExit(f"Cannot search X: {why}.")
    now = time.time()
    seeds = [s for s in X.seeds(cache, trending=X.trending_now()) if s.search()]
    found, reads = [], 0
    for seed in seeds:
        if reads + 10 > max_reads:
            break
        posts = await X.search(seed.search(), max_results=10, since=now - 24 * 3600)
        reads += len(posts)
        found.extend(p for p in posts if not X.screen(p, now, window_hours=24, min_engagement=0))
    out, authors = [], set()
    for p in found:
        if p.author not in authors:
            authors.add(p.author)
            out.append((p, []))
        if len(out) >= n:
            break
    return out


def page(rows: list) -> str:
    e = lambda s: html.escape(str(s or ""))  # noqa: E731
    cells = []
    for i, r in enumerate(rows, 1):
        p = r["post"]
        if r["episode"] is not None:
            ep = f"<b>{e(r['episode'].title)}</b><br><small>kept · {r['episode'].minutes} min</small>"
        elif r["proposed"]:
            ep = (f"<b>Proposed:</b> {e(r['proposed']['title'])}<br><small>{e(r['proposed'].get('scope'))}"
                  f"</small><br><small>not made yet</small>")
        else:
            ep = "<i>no episode</i>"
        flags = []
        if r["by"] == "code":
            flags.append("read/drafted in code")
        if r["anchored"] is False:
            flags.append("talks past the post")
        if r["promo"]:
            flags.append("sounds like an ad: " + ", ".join(r["promo"]))
        replies = "".join(f"<li>{e(t.text)}</li>" for t in r["replies"])
        cells.append(f"""<tr>
<td class=n>{i}</td>
<td><a href="{e(p.url)}">@{e(p.author)}</a><p>{e(p.text)}</p>{f'<details><summary>{len(r["replies"])} replies (context)</summary><ul>{replies}</ul></details>' if replies else ''}</td>
<td><p><b>Means:</b> {e(r['central'])}</p><p><b>FAM adds:</b> {e(r['angle'])}</p></td>
<td>{ep}</td>
<td>{e(r['reply']) if r['reply'] else '<i>none</i>'}{''.join(f'<div class=flag>{e(f)}</div>' for f in flags)}</td>
<td><b>{e(r['relevance'])}/5</b> {e(X.RELEVANCE.get(r['relevance'] or 0, ''))}<p>{e(r['why'])}</p>
{'' if r['author_test'] else '<div class=flag>fails the original-author test</div>'}
<p><b>{e(X.RECOMMENDATIONS.get(r['recommendation'], r['recommendation']))}</b></p>
{f'<small>{e(r["dropped"])}</small>' if r['dropped'] else ''}</td>
<td class=rev>Agree / disagree:<br><br>Better comment:<br><br></td>
</tr>""")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>X Agent Review</title>
<style>
:root{{--bg:#fff;--ink:#1a1a1a;--line:#ddd;--muted:#666;--flag:#a33}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#16131c;--ink:#eee;--line:#3a3348;--muted:#a79eba;--flag:#de8fa8}}}}
body{{background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;padding:16px}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;min-width:1100px}}
th,td{{border:1px solid var(--line);padding:8px;vertical-align:top;text-align:left}}
th{{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}}
td p{{margin:4px 0}} .n{{color:var(--muted)}} .flag{{color:var(--flag);font-size:12px;margin-top:4px}}
.rev{{min-width:160px;color:var(--muted)}} a{{color:inherit}}
</style></head><body>
<h1>The X agent against {len(rows)} posts</h1>
<p>Every row is pending human review. Episode + comment needs relevance 4-5 and the original-author test;
3 is a comment only; 1-2 is rejected (§366).</p>
<div class=scroll><table><tr><th>#</th><th>Original post</th><th>Central message / opportunity</th>
<th>Episode</th><th>Proposed comment</th><th>Relevance / recommendation</th><th>Reviewer</th></tr>
{''.join(cells)}</table></div></body></html>"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--posts", help="JSON list of {url, text, replies?}")
    ap.add_argument("--from-x", type=int, default=0, help="find this many posts on X instead")
    ap.add_argument("--max-reads", type=int, default=200)
    ap.add_argument("--out", default="x_agent_eval.html")
    args = ap.parse_args()
    from cache import build_cache

    cache = build_cache()
    episodes = OI.catalogue(cache)
    if args.from_x:
        posts = await from_x(cache, args.from_x, args.max_reads)
    elif args.posts:
        rows = json.loads(pathlib.Path(args.posts).read_text())
        posts = []
        for n, row in enumerate(rows):
            post = post_from(row, n)
            replies = [X.Post(id=f"{post.id}r{i}", text=t, created_at=post.created_at,
                              author="reply", is_reply=True)
                       for i, t in enumerate(row.get("replies") or [])]
            posts.append((post, replies))
    else:
        ap.error("give --posts or --from-x")
    results = [await judge(cache, candidate(p, episodes, r)) for p, r in posts]
    pathlib.Path(args.out).write_text(page(results), encoding="utf-8")
    spent = sum(r["dollars"] for r in results)
    tally = {}
    for r in results:
        tally[r["recommendation"]] = tally.get(r["recommendation"], 0) + 1
    print(f"{len(results)} posts against {len(episodes)} kept episodes: {tally}; "
          f"${spent:.4f} drafting; wrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
