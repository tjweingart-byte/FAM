# FAM official documentation

Formal reference documentation for FAM: how it works end to end, what it
costs, what it stores, and everything the owner has asked for. A new person
starts with **ONBOARDING.md** and follows its curriculum.

| Document | Answers |
|---|---|
| [**ONBOARDING.md**](ONBOARDING.md) | The entry point for training. What FAM is, the vocabulary, the architecture in one picture, the life of one episode, the rules newcomers break, a day-by-day curriculum, how to work and ship, how to operate production, and where to look anything up. |
| [**BACKEND.md**](BACKEND.md) | How the server is wired. The life of a search episode stage by stage, every background job and its schedule, where every rail and section is sourced from and its fallbacks, every route, every call to an outside service, the admin surfaces, staging vs production. |
| [**FRONTEND.md**](FRONTEND.md) | How the app is wired. Boot, the screen system and navigation stack, every screen and the endpoints it calls, search, the player and its one transport, audio streaming in the browser, offline, social, share pages, admin pages, and how the previews and smoke run test it. |
| [**DATA.md**](DATA.md) | What is stored and where (Render disk, RunPod volume, process memory, the phone). How much. The guardrails and size caps. How long each thing is kept. The database-to-tile wiring of the ranking algorithm (the full algorithm is generated in `algorithm/ALGORITHM.md`). |
| [**PRODUCT_HISTORY.md**](PRODUCT_HISTORY.md) | Everything the owner has asked for, by area, with the PROBLEMS.md section where it was decided and whether it is current, changed or reversed. The reversals, and what is still open. |
| [**FINANCIAL.md**](FINANCIAL.md) | What each part of the process costs per episode and per listener. How the bill changes from 100 to 100,000 listeners. At what point each provider (RunPod, Render, API-Sports, Finnhub, Polymarket, GNews, GDELT, Exa, NWS, Open-Meteo, Claude, Chatterbox) hits a rate limit, and what the next step costs. |
| [**SCALING_TIMELINE.md**](SCALING_TIMELINE.md) | When to change each outside service, by stage, by a number you can see on `/admin`, or by a provider's date. What to change it to, and what it costs. The launch checklist. |
| [**algorithm/ALGORITHM.md**](algorithm/ALGORITHM.md) | The ranking algorithm, generated from the code by `tools/algorithm_docs.py` (§202); a test fails when it is stale. Not in the published page; it has its own PDF and deck beside it. |

## Rules these documents follow

- **Every number is labelled.** It is either a *code* constant (with file and
  line), a *measured* result from `PROBLEMS.md`, a provider's *list price*
  (dated), or an *estimate* with its working shown.
- **Where a document disagrees with the code, the code wins.** Fix the
  document.
- **The design reasoning lives elsewhere.** `CLAUDE.md` says where the
  product is going and what is settled. `PROBLEMS.md` is the engineering log.
  The topic files (`MYFAM.md`, `TRENDING.md`, `LIVE_FACTS.md`, `METERING.md`,
  `REMOTE_VOICE.md`, `DATABASE.md` and the rest) explain *why*. These
  documents are the *what*, in one place.

## Keeping them current

Update the relevant document in the same change whenever you:

- change a price, budget, TTL, cap or schedule constant in `config.py` or a
  module
- add or remove a SQLite store, a background job, or an external provider
- change where a myFAM rail draws from, or its fallback
- add, remove or rewire a screen, an endpoint a screen calls, or the player
  (`FRONTEND.md`), or a route or background job (`BACKEND.md`)
- make a decision the owner asked for (`PRODUCT_HISTORY.md`, one row, with
  its PROBLEMS.md section)

Replace the estimates in `FINANCIAL.md` §4 with real numbers from
`python tools/usage_report.py` once there is a month of production traffic.

## The published version

Every document in the table except `algorithm/ALGORITHM.md` is published as one page:
<https://claude.ai/artifact/1KQ5xwih3Tum77ixi8Gr7E>

Rebuild it from the markdown with `python docs/build_html.py` (it needs
`pip install markdown mdx_truly_sane_lists`), then republish to that URL, so the link never
changes.
