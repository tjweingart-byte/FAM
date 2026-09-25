# FAM official documentation

Formal reference documentation for FAM. It covers finances, how the backend
works, and what data is stored.

| Document | Answers |
|---|---|
| [**FINANCIAL.md**](FINANCIAL.md) | What each part of the process costs per episode and per listener. How the bill changes from 100 to 100,000 listeners. At what point each provider (RunPod, Render, API-Sports, Finnhub, Polymarket, GNews, GDELT, Exa, Claude, Chatterbox) hits a rate limit, and what the next step costs. |
| [**BACKEND.md**](BACKEND.md) | Episode latency, stage by stage, for search and for the background browse path. Where every section (Trending, Made for You, Go Deeper, What FAM Can't Stop Listening To, Friends, What You Missed, SearchFAM, DailyFAM, ExploreFAM) is sourced from, the path FAM takes to produce it, and its fallbacks. Every call site of every external service. |
| [**DATA.md**](DATA.md) | What is stored and where (Render disk, RunPod volume, process memory, the phone). How much. The guardrails and size caps. How long each thing is kept. The database-to-tile wiring of the ranking algorithm, including the numpy matrix similarity step and the learned re-order. |

## Rules these documents follow

- **Every number is labelled.** It is either a *code* constant (with file and
  line), a *measured* result from `PROBLEMS.md`, a provider's *list price*
  (dated), or an *estimate* with its working shown.
- **Where a document disagrees with the code, the code wins.** Fix the
  document.
- **The design reasoning lives elsewhere.** `CLAUDE.md` says where the
  product is going and what is settled. `PROBLEMS.md` is the engineering log.
  The topic files (`MYFAM.md`, `TRENDING.md`, `LIVE_FACTS.md`, `METERING.md`,
  `REMOTE_VOICE.md`, `DATABASE.md` and the rest) explain *why*. These three
  documents are the *what*, in one place.

## Keeping them current

Update the relevant document in the same change whenever you:

- change a price, budget, TTL, cap or schedule constant in `config.py` or a
  module
- add or remove a SQLite store, a background job, or an external provider
- change where a myFAM rail draws from, or its fallback

Replace the estimates in `FINANCIAL.md` §4 with real numbers from
`python tools/usage_report.py` once there is a month of production traffic.

## The published version

The same three documents are published as one page:
<https://claude.ai/artifact/1KQ5xwih3Tum77ixi8Gr7E>

Rebuild it from the markdown with `python docs/build_html.py` (it needs
`pip install markdown mdx_truly_sane_lists`), then republish to that URL, so the link never
changes.
