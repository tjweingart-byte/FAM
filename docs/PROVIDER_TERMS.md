# Provider terms: may FAM keep and reuse what its providers return?

| | |
|---|---|
| **Status** | Research note for the owner (PROBLEMS.md §340). Nothing here changes code or a rule book |
| **Written** | 2026-10-29 |
| **Asked** | May FAM store what Parallel, Perplexity, GNews and Polymarket return, for how long, reuse it for another listener's episode, and speak it to the public - and what must be credited |
| **Settled, not reopened** | GDELT is open for any use with a citation and a link to gdeltproject.org; Tiingo (prices) and BALLDONTLIE (scores) are approved - the owner has agreed redistribution with both; Finnhub and API-Sports are being removed from FAM |
| **Where the decision goes** | Once the owner decides, the branch that owns `docs/FAM_AGENTS.md` (the Housekeeper tab's table), `docs/AGENTS_BUILD_PROMPT.md` (item 8) and `docs/agent_rules/consuela.md` (never 8) carries it into them. This file does not edit them |

This is not legal advice. It reads what the providers publish and lists what a
lawyer should confirm; it decides nothing for FAM.

## The answer

**None of the four open providers' terms could be read on 2026-10-29, so none
of them can be marked allowed.** Every page on parallel.ai, perplexity.ai,
gnews.io and polymarket.com (and their docs sites) was refused by this
session's network policy before a byte came back - the clauses section lists
every address tried. So, as of today: GDELT, Tiingo and BALLDONTLIE stay
approved as settled; **Parallel, Perplexity, GNews and Polymarket are
unclear on every question**, and Consuela keeps none of their material
(build-prompt item 8's switch stays off for them). One thing does not wait for
the pages: **production already keeps Parallel's and Perplexity's words** -
`history_archive` intake stores up to eight search passages per article
(Atlas in production), up to 500,000 articles, without recording which provider supplied them
(see *What FAM keeps today*). Until the answers come, the safest shape for the
two search providers is to keep **links, titles, dates, outlet and grade, never
the passages**, and to re-search when the words are needed; for GNews, a paid
plan before launch; for Polymarket, the numbers only, credited as a forecast.
The four draft emails at the end ask each provider in plain terms; the
pages should be read in a session whose network allows them (*To finish
this*), and the table re-marked from the quotes.

## The table

Marks: **allowed**, **not allowed**, **unclear**, **needs a different plan**.
"Unread" means the provider's own page could not be opened this session; no
answer here comes from memory or a search snippet.

| Provider | 1. Store at all? | 2. How long? | 3. Reuse for another listener's episode? | 4. Speak it (paraphrased, credited)? | 5. Credit or link | Safest shape now | Next |
|---|---|---|---|---|---|---|---|
| **GDELT** | Allowed (settled) | Allowed - no limit (settled) | Allowed (settled) | Allowed (settled) | Cite GDELT and link to gdeltproject.org (settled) | Titles, links, dates and themes, cited | Nothing - settled. The titles are still the publishers' words (*Copyright*) |
| **Tiingo** (prices) | Allowed (owner's agreement) | Per the agreement | Allowed (owner's agreement) | Allowed (owner's agreement) | Whatever the agreement names (not seen here) | Every daily price, kept as Consuela's rule 6 says | Put the agreement's credit wording in the rule book |
| **BALLDONTLIE** (scores) | Allowed (owner's agreement) | Per the agreement | Allowed (owner's agreement) | Allowed (owner's agreement) | Whatever the agreement names (not seen here) | Every final and in-game score | As Tiingo |
| **Parallel** (Search API) | Unclear (unread) | Unclear (unread) | Unclear (unread) - the clause the agents guide flagged, "storing Output for other End Customers", is exactly this question | Unclear (unread) | Unclear (unread) | Links, titles, dates, outlet, grade; **no passages**; re-search for the words | Send email 1; read the Customer Terms |
| **Perplexity** (Search API) | Unclear (unread) | Unclear (unread) | Unclear (unread) | Unclear (unread) | Unclear (unread) | As Parallel | Send email 2; read the API terms and the Search API addendum |
| **GNews** (Trending) | Unclear (unread) | Unclear (unread) | Unclear (unread) | Unclear (unread); FAM uses no GNews words in an episode today | Unclear (unread) | Titles and links for the edition's life (12 hours), the count, nothing after; **a paid plan before launch** if the free plan is non-commercial | Send email 3; read the terms and pricing |
| **Polymarket** (forecasts) | Unclear (unread) | Unclear (unread) | Unclear (unread) | Unclear (unread) | Unclear (unread); FAM already says "a forecast, not a result" | The numbers only - market, outcome, price, time, link - credited by name | Send email 4; read the Terms of Use and any API policy |
| ~~Finnhub~~, ~~API-Sports~~ | - | - | - | - | - | Nothing kept | Being removed (settled) |

Not in the agents guide's table and not reviewed here: Wikipedia (read for hard
names, §289), the National Weather Service and Open-Meteo (weather), and the
town outlets' RSS feeds (local news). Each has terms of its own; ask for them if
Consuela is to keep anything they return.

## What FAM sends and receives today

Read from the code on this branch, 2026-10-29.

| Provider | FAM sends | FAM receives | What FAM keeps today |
|---|---|---|---|
| **Parallel** (`research._search_parallel`, `POST https://api.parallel.ai/v1/search`) | Up to three short keyword queries from EI (each ≤200 characters), an objective, an after-date, an outlet allow-list, a country | Per result: URL, title, publish date, markdown excerpts up to `PARALLEL_CHARS_PER_RESULT` (4,000 characters) | The episode's script (FAM's words) in the cache for a week (kept longer when saved or shared); the article links in `episode_sources`; **up to 8 passages per article in `history_archive` intake**, merged across episodes, kept until 500,000 articles (`ARCHIVE_INTAKE_MAX`) |
| **Perplexity** (`research._search_perplexity`, `POST https://api.perplexity.ai/search`) | The query, a result count (≤20), an after-date, an outlet list, a country, a search mode (SEC, academic) | Per result: URL, title, date, a snippet up to `PERPLEXITY_TOKENS_PER_PAGE` (1,024 tokens) | As Parallel - the intake does not record which of the two supplied a passage |
| **GNews** (`gnews.py`, `https://gnews.io/api/v4`) | `top-headlines` by category and country; `search` by a story's words over twelve hours | Articles (title, description, URL, source, date) and `totalArticles` | The edition's stories: headlines (`titles`) and `urls` in the trending bank, plus FAM's composed tile. Never evidence: a tapped tile is researched from scratch |
| **Polymarket** (`live_sources.PolymarketSource`, `https://gamma-api.polymarket.com`, `/public-search`) | The question's subject, a few words | Events and markets: question, outcome prices, volume | Nothing as a record: the forecast is read to the writer as "a forecast, not a result" and credited in provenance; the script that used it is cached like any other |
| **GDELT** (`gdelt.py`, export files) | Nothing a listener does; one job downloads the 15-minute files | Article URLs, titles, dates, themes, tone | A rolling copy on disk; titles and links in packets and intake |

**Two findings, for the owner rather than for this file to fix:**

1. **Intake already keeps search passages.** The terms question is not only
   about Consuela: today's `history_archive.intake` (§308) stores the passages
   Parallel and Perplexity returned, and production keeps the archive in
   Atlas (§339; `ARCHIVE_BACKEND` is set in the dashboard). If either provider says no, those passages are
   already held. Deciding whether to stop intake keeping passages now, or keep
   them until the answers come, is the owner's call.
2. **Intake does not record the provider.** Build-prompt item 8 switches
   storage on per provider; a record that does not say which provider it came
   from cannot be switched off or deleted per provider. Whatever is decided,
   intake should record `parallel`, `perplexity` or `gdelt` beside each
   passage before Consuela is built.

## The safest shapes, and what each costs

The provider's terms decide whether FAM may keep the *provider's output*; the
publisher's copyright decides whether FAM may keep the *publisher's words*
(next section). A shape is safe only when it passes both.

| Shape | Safe under | What it costs FAM |
|---|---|---|
| **A. Links, titles, dates, outlet and grade only; re-search for the words** | Most API terms that restrict caching still let a customer keep a link; a link and a date are not the publisher's expression (a title may be - see *Copyright*) | Sonic's recall becomes a list of where to look, not what was said: an old figure or quote has to be found again by a new search ($0.001 each, about half a second, and the page may have moved or been paywalled). Trends over years must be rebuilt from figures FAM kept elsewhere (Tiingo, BALLDONTLIE, GDELT) |
| **B. FAM's own one-line digest with the link** (FAM's words, not the provider's) | Usually the safest for copyright; under the provider's terms it is still "derived from Output", which some terms also cover | **Conflicts with Consuela's rule book**: never 1 says she may never keep her own summary as a fact, and must-always 3 says key sentences are "never reworded". Choosing B means the owner changes those rules on purpose. A paraphrase can also drift from what the source said - the reason the rule exists |
| **C. Keep the words for a limited time** (for example, passages for 30 or 90 days, links and dated figures after) | Fits terms that allow caching for a period, if the period matches theirs | Recall of last month's reporting works; "what did they say two years ago" does not. Consuela's must-always 4 (consolidate at 90 days) is already halfway there |
| **D. A written agreement or enterprise plan that allows it** | Settles the provider's side, as the owner did with Tiingo and BALLDONTLIE | Money and a negotiation; does not settle the publishers' copyright |
| **E. Facts as data: the figure, its unit, its date, its link** (a score, a price, a count, a vote total), never the sentence it came in | Facts are not the publisher's expression; under provider terms, still Output | Keeps what §313 says is most valuable (scores and prices) - and those mostly come from Tiingo and BALLDONTLIE, which are approved |

**Recommended until the answers come:** A for Parallel and Perplexity (with E
for figures only if their terms allow storing Output at all), titles and links
for the edition's life for GNews, the numbers alone for Polymarket. Revisit per
provider as each answer arrives; if a provider allows storage for a period,
move it to C.

## The clauses, with addresses and dates

Item 1 of the request was to quote the clauses that decide each question from
the provider's own pages. **None could be opened.** On 2026-10-29 every
request below was refused by this cloud session's network policy: from the
container, `curl` got `CONNECT tunnel failed, response 403` from the proxy; the
page reader got `getaddrinfo ENOTFOUND` for each host (the host is outside the
session's allowed domains). Nothing below is a quote, and no answer above was
filled in from memory or a search snippet.

**Parallel** - could not open, 2026-10-29:
- https://parallel.ai/customer-terms (and `/customer-terms.md`)
- https://docs.parallel.ai/resources/faqs.md
- https://parallel.ai/pricing
- https://parallel.ai/terms-of-service
- https://parallel.ai/acceptable-use-policy
- https://docs.parallel.ai/search/search-quickstart

**Perplexity** - could not open, 2026-10-29:
- https://www.perplexity.ai/hub/legal (the index, to find the Search API addendum)
- https://www.perplexity.ai/hub/legal/perplexity-api-terms-of-service
- https://www.perplexity.ai/hub/legal/api-terms-of-service
- https://www.perplexity.ai/hub/legal/search-api-addendum
- https://www.perplexity.ai/hub/legal/terms-of-service
- https://www.perplexity.ai/hub/legal/aup
- https://docs.perplexity.ai/getting-started/pricing
- https://docs.perplexity.ai/guides/usage-tiers
- https://docs.perplexity.ai/faq

**GNews** - could not open, 2026-10-29:
- https://gnews.io/terms
- https://gnews.io/pricing
- https://gnews.io/llms.txt
- https://gnews.io/
- https://gnews.io/docs/v4
- https://gnews.io/faq

**Polymarket** - could not open, 2026-10-29:
- https://polymarket.com/tos (and `/terms-of-use`)
- https://polymarket.com/api-terms
- https://polymarket.com/brand
- https://docs.polymarket.com/quickstart/introduction/rate-limits
- https://docs.polymarket.com/developers/gamma-markets-api/overview
- https://docs.polymarket.com/api-usage-policy
- https://www.polymarketexchange.com/files/legal/latest/participant-agreement

**Leads only - what to look for when the pages open.** These came from web
search results on 2026-10-29 and from the agents guide's earlier cached copy.
They decide nothing; each must be checked against the live page before the
table changes.
- Parallel: the Customer Terms define "Customer Output" as what the Services
  return to Authorized Users for an input; the agents guide's cached copy
  said Output is "primarily for the exclusive use of the Authorized User who
  submitted the corresponding query" and restricts "storing Output for other
  End Customers"; the FAQ is reported to say the customer owns the output;
  a Google Cloud Marketplace agreement and enterprise zero-data-retention are
  mentioned. Read the definitions of Authorized User and End Customer - whether
  a FAM listener is an "End Customer" is the whole question.
- Perplexity: the agents guide's earlier reading said the Search API addendum
  says the customer does not own Search API output (it is public web content);
  the display and storage clauses were not read then either.
- GNews: the home page is reported to describe the free plan as "for
  non-commercial projects or for development and testing purposes only", with
  paid plans from about €49.99 a month; `llms.txt` points at `/pricing` and
  `/terms`.
- Polymarket: the Polymarket Exchange participant agreement refers to "the
  Polymarket Terms of Service and API Usage Policy" - look for that policy;
  whether it covers the public Gamma API is not known.

Settled providers were not re-read: GDELT's terms (cite and link), and the
owner's agreements with Tiingo and BALLDONTLIE, stand as the owner recorded
them on 2026-10-28.

## The publishers' copyright, separately

A provider's terms and a publisher's copyright are two different permissions.
Parallel or Perplexity can only license what they own; the words in a search
excerpt were written by the outlet that published them, **whatever the
provider allows**. A provider saying "you own the Output" (as Parallel's FAQ
is reported to) cannot give FAM the publisher's sentences; a provider saying
"you do not own it" (as Perplexity's addendum is reported to) is saying
exactly this.

What that means for the shape Consuela's rule book asks for - key sentences
kept verbatim, with a link, indefinitely:

- **Speaking an episode** that paraphrases and credits what was reported is
  the lowest-risk use: FAM's script is FAM's words, and facts themselves are
  not anyone's expression.
- **Keeping short verbatim sentences** is a copy of the publisher's words,
  made for a commercial product and kept for years. Short, attributed,
  linked and internal lowers the risk; it does not remove it. Scale matters:
  500,000 articles' passages is a database of other people's words, not a
  handful of quotes.
- **A headline is the publisher's too.** Titles are usually treated as too
  short to protect, but not everywhere, and the European press publishers'
  right covers more than full articles.
- **Figures, dates, scores and names are facts**, kept as data (shape E) -
  the lowest-risk thing to keep, and the most valuable (§313).
- **Opinion pieces are already excluded** (Consuela never 6), which removes
  the most expressive material.

What a lawyer should confirm before launch:

1. Whether keeping short verbatim excerpts (one to three sentences, with the
   link and outlet) indefinitely, for internal recall only, never shown or
   read out verbatim, is fair use in the US for a commercial product - and
   whether the answer changes if a sentence is later spoken in an episode.
2. Whether the EU press publishers' right (Article 15 of the DSM Directive)
   and the UK and EU text-and-data-mining exceptions reach FAM if it has
   European listeners or stores data in Europe, and whether headlines and
   "very short extracts" are inside them.
3. Whether a FAM-written one-line digest (shape B) is treated as a
   derivative work of the article, and whether that matters for internal use.
4. Whether each provider's restriction on storing or reusing Output survives
   the publisher's own permission (for instance, an outlet whose site allows
   copying), and whether facts extracted from Output are still "Output".
5. Whether credit in the episode ("according to Reuters") and the sources list
   on the player are enough attribution under each provider's terms and the
   publishers' expectations - and whether GDELT's citation needs to appear in
   the app, the episode, or the Terms.
6. Whether FAM's Terms of Service and Privacy Policy must say that FAM keeps
   an archive of third-party reporting, and how a publisher asks for removal
   (a takedown address and process).
7. Whether a removal request from a publisher must reach the cached scripts
   and kept audio that paraphrase it, or only the archive's stored words.
8. Whether the passages already in Atlas from intake (*Two findings*) should
   be deleted, kept, or kept pending the providers' answers.

## Draft emails (not sent)

From Admin@familiarize.net, sent by the owner. Each provider's address is left
for the owner to take from its own contact or support page - none was
verified this session.

### 1. Parallel

> **To:** Parallel's support or sales address (from parallel.ai)
> **Subject:** Storing Search API results - is our use allowed?
>
> Hello,
>
> We run FAM, a consumer app that turns news questions into short spoken
> episodes. We use Parallel's Search API (`/v1/search`, fast mode) with our
> own API key: for each listener's question we search, read the results, and
> write an episode in our own words that credits the outlets.
>
> We would like to keep part of what the Search API returns, and want to
> check it is allowed before we do:
>
> 1. May we store the results (URL, title, publish date and a few key
>    sentences from the excerpts) in our own database? If so, for how long?
> 2. May we use something stored for one listener's question again when
>    writing an episode for a different listener? The stored text would never
>    be shown or resold; it would be read by our system to write a new
>    episode in our own words.
> 3. May an episode that paraphrases those results, crediting the publisher,
>    be heard by the public?
> 4. What, if anything, must we credit or link to Parallel?
>
> If any of this is not allowed on our current plan, what plan or agreement
> would allow it, and what would it cost?
>
> Thank you,
> [Owner's name], FAM - Admin@familiarize.net

### 2. Perplexity

> **To:** Perplexity's API support address (from docs.perplexity.ai)
> **Subject:** Storing Search API results - is our use allowed?
>
> Hello,
>
> We run FAM, a consumer app that turns news questions into short spoken
> episodes. We use the Perplexity Search API (`/search`) with our own API
> key as a second search when our first one comes back thin; we write each
> episode in our own words and credit the outlets.
>
> Before we keep anything, could you confirm, under the API terms and the
> Search API addendum:
>
> 1. May we store the results (URL, title, date and a few sentences of the
>    snippet) in our own database? For how long?
> 2. May something stored for one user's question be used again, internally,
>    to write an episode for a different user? It would never be displayed or
>    resold.
> 3. May an episode that paraphrases the results, crediting the publisher,
>    be heard by the public?
> 4. Must we credit or link to Perplexity, or display the source URLs?
>
> If our current plan does not allow this, which plan or agreement would?
>
> Thank you,
> [Owner's name], FAM - Admin@familiarize.net

### 3. GNews

> **To:** GNews's contact address (from gnews.io)
> **Subject:** Commercial use of top headlines and search - which plan?
>
> Hello,
>
> We run FAM, a consumer app that turns news into short spoken episodes. We
> use GNews twice a day (`top-headlines` by category and country, and
> `search` over the last twelve hours) only to decide which stories are
> trending. We never read GNews's text in an episode: each story is
> researched separately and written in our own words.
>
> 1. FAM is a commercial product. Which GNews plan covers that use?
> 2. May we store the headlines, links and publish dates we receive? For
>    how long?
> 3. May we show our own tile for a trending story (our own title, not the
>    headline) to all our users, and use the article count to rank it?
> 4. What must we credit or link to GNews?
>
> Thank you,
> [Owner's name], FAM - Admin@familiarize.net

### 4. Polymarket

> **To:** Polymarket's developer or partnerships address (from docs.polymarket.com)
> **Subject:** Quoting public market odds in a news app - is it allowed?
>
> Hello,
>
> We run FAM, a consumer app that turns news questions into short spoken
> episodes. When a question turns on an outcome (an election, a ruling, a
> final), we look up the subject on the public Gamma API (`/public-search`)
> and may say, for example, "Polymarket traders give this a 60% chance - a
> forecast, not a result". We do not trade, and our users do not place bets
> through us.
>
> 1. May we use the public API this way in a commercial product, and say
>    the odds aloud, crediting Polymarket?
> 2. May we store the odds we read (market, outcome, price, time) to show
>    how a forecast changed over time? For how long?
> 3. What credit or link does Polymarket require, and may we use the name
>    "Polymarket" in an episode?
> 4. Does it matter where our listeners are (for example, in the US)?
>
> If this needs an agreement or a data licence, what would it be?
>
> Thank you,
> [Owner's name], FAM - Admin@familiarize.net

## To finish this

The quotes in item 1 need a session whose network can reach the providers.
In the environment's settings (the cloud environment menu in the session's
title bar, then Edit), Network access: add these under Allowed domains, or
choose a broader level (https://code.claude.com/docs/en/cloud-environments#network-access):

`parallel.ai`, `docs.parallel.ai`, `www.perplexity.ai`, `docs.perplexity.ai`,
`gnews.io`, `polymarket.com`, `docs.polymarket.com`, `www.polymarketexchange.com`

Then ask again: read every page listed under *The clauses*, put each deciding
clause here word for word with its address and the date read, and re-mark
the table. A provider's written reply to an email above outranks its page,
and goes here quoted the same way.
