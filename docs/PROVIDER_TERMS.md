# Provider terms: may FAM keep and reuse what its providers return?

| | |
|---|---|
| **Status** | Research note for the owner (PROBLEMS.md §341, finished in §342; email 3 sharpened in §343). Nothing here changes code or a rule book |
| **Written** | 2026-10-29 (first pass: no page could be opened); **finished 2026-10-29** from the providers' own pages, every quote below read that day |
| **Asked** | May FAM store what Parallel, Perplexity, GNews and Polymarket return, for how long, reuse it for another listener's episode, and speak it to the public - and what must be credited |
| **Settled, not reopened** | GDELT is open for any use with a citation and a link to gdeltproject.org; Tiingo (prices) and BALLDONTLIE (scores) are approved - the owner has agreed redistribution with both; Finnhub and API-Sports are being removed from FAM |
| **Where the decision goes** | Once the owner decides, the branch that owns `docs/FAM_AGENTS.md` (the Housekeeper tab's table), `docs/AGENTS_BUILD_PROMPT.md` (item 8) and `docs/agent_rules/consuela.md` (never 8) carries it into them. This file does not edit them |

**This is not legal advice.** It reads what the providers publish, quotes the
clauses that decide each question, and lists what a lawyer should confirm; it
decides nothing for FAM.

## The answer

**Parallel says no to the thing Consuela wants.** Its Customer Terms say
output "generated from one query shall be primarily for the use of one End
Customer only, and shall not be copied, cached, stored, or made available to
other End Customers" (§2(b)), and forbid "making available Customer Outputs
from previous queries for future uses" (§2(c)(xi)). So keeping Parallel's
passages to write a *different* listener's episode is **not allowed** on the
standard terms - and `history_archive` intake already does exactly that today
(*Two findings*). Whether FAM's own cached script, written from Parallel's
output and replayed to the next listener who asks, falls under the same words
is **unclear** (the proviso names Customer Output, the permission names
derivative works, and "primarily" is undefined); that, and what an enterprise
agreement would allow, are what email 1 asks. **Perplexity's terms do not
restrict storing or reusing Search API results** - its own docs describe
results "for your own code to filter, store, or hand to a model" - but
Perplexity also says FAM "does not own" them and gives no IP indemnity for
them, so the publishers' copyright is the only limit; Perplexity requires no
credit. **GNews allows storing and reusing what a paid plan retrieves**, even
after the plan ends, and requires no credit - but **only a paid plan may be
used commercially**, and FAM in production is commercial, so the edition needs
Essential (€49.99 a month) now, not at launch. **Polymarket's Terms bind API
use, grant only a "personal" licence, and say nothing about storing or quoting
odds**; they bar only capital-markets firms and market data distributors - so
storing and speaking odds stay **unclear** and email 4 asks. Until the answers
come: for Parallel, keep **links, titles, dates, outlet and grade, never the
passages**, and stop intake keeping Parallel's words (the owner's call); for
Perplexity, passages may be kept as far as Perplexity is concerned, subject to
the copyright section; for GNews, a paid plan; for Polymarket, the numbers
only, credited as a forecast, and nothing stored past the episode.

## The table

Marks: **allowed**, **not allowed**, **unclear**, **needs a different plan**.
Every mark for the four open providers cites a clause quoted under *The
clauses*, read on 2026-10-29.

| Provider | 1. Store at all? | 2. How long? | 3. Reuse for another listener's episode? | 4. Speak it (paraphrased, credited)? | 5. Credit or link | Safest shape now | Next |
|---|---|---|---|---|---|---|---|
| **GDELT** | Allowed (settled) | Allowed - no limit (settled) | Allowed (settled) | Allowed (settled) | Cite GDELT and link to gdeltproject.org (settled) | Titles, links, dates and themes, cited | Nothing - settled. The titles are still the publishers' words (*Copyright*) |
| **Tiingo** (prices) | Allowed (owner's agreement) | Per the agreement | Allowed (owner's agreement) | Allowed (owner's agreement) | Whatever the agreement names (not seen here) | Every daily price, kept as Consuela's rule 6 says | Put the agreement's credit wording in the rule book |
| **BALLDONTLIE** (scores) | Allowed (owner's agreement) | Per the agreement | Allowed (owner's agreement) | Allowed (owner's agreement) | Whatever the agreement names (not seen here) | Every final and in-game score | As Tiingo |
| **Parallel** (Search API) | **Unclear** for the listener who asked (P1 forbids storing only "to other End Customers"); **not allowed** to build a database (P2 (vi)(B)) | Unclear - no period named; the use, not the time, decides | **Not allowed** for the output itself (P1 (i), P2 (xi), P3); **unclear** for FAM's own script derived from it (P1's permission for "derivative works") - **needs a different plan** (enterprise agreement) to allow it | **Allowed** to the listener who asked, as a derivative work (P1); unclear for replays to others (as col. 3) | **None required** (no attribution clause); do not imply the words are human-written (P5) | Links, titles, dates, outlet, grade; **no passages**; re-search for the words; intake keeps no Parallel words | Send email 1; owner decides on intake's Parallel passages; FAM's Terms must bind listeners (P4) |
| **Perplexity** (Search API) | **Allowed** by Perplexity's terms (no restriction; Q4 "filter, store"); the publishers' copyright still applies (Q3) | **Allowed** - no limit named | **Allowed** by Perplexity's terms (no restriction found); copyright as above | **Allowed** to show "solely within the Customer Applications" (Q1); paraphrase in FAM's app is that | **None required** (no attribution clause); no misrepresenting provenance (Q6) | Passages may be kept *as far as Perplexity is concerned*; keep them short, linked and internal (*Copyright*); send no personal data (Q5) | Short email 2 (personal data in queries; audio as "display"); lawyer item 1 |
| **GNews** (Trending) | **Allowed** on a paid plan (G3, G4); Free-plan data never for commercial use (G1, G2) | **Allowed** - kept even after the plan ends (G3), unless terminated for cause (G6) | **Allowed** (no per-user restriction; one fetch for everyone is fine); no competing database or redistribution (G4) | FAM speaks no GNews text; publishers' content stays theirs (G5) | **None required** ("appreciated but not required", G5); never present the data as FAM's own (G5) | Titles and links for the edition's life; **a paid plan now** - FAM in production is commercial (G2) | Subscribe to Essential (no email needed); check which plan the production key is on |
| **Polymarket** (forecasts) | **Unclear** - no storage clause; licence is "personal" (M2) | Unclear | Unclear (M2) | **Unclear** - not barred (FAM is no Capital Market Client, M3), but the licence is "personal" and "market data distributor" is undefined | **None required** by the Terms; brand page asks for the wordmark "as space allows" (M4) - none in audio | The numbers only - market, outcome, price, time, link - credited by name, nothing kept past the episode | Send email 4 to hello@polymarket.com |
| ~~Finnhub~~, ~~API-Sports~~ | - | - | - | - | - | Nothing kept | Being removed (settled) |

Not in the agents guide's table and not reviewed here: Wikipedia (read for hard
names, §289), the National Weather Service and Open-Meteo (weather), and the
town outlets' RSS feeds (local news). Each has terms of its own; ask for them if
Consuela is to keep anything they return.

## What FAM sends and receives today

Read from the code on this branch, 2026-10-29.

| Provider | FAM sends | FAM receives | What FAM keeps today |
|---|---|---|---|
| **Parallel** (`research._search_parallel`, `POST https://api.parallel.ai/v1/search`) | Up to three short keyword queries from EI (each ≤200 characters), an objective, an after-date, an outlet allow-list, a country | Per result: URL, title, publish date, markdown excerpts up to `PARALLEL_CHARS_PER_RESULT` (4,000 characters) | The episode's script (FAM's words) in the cache for a week (kept longer when saved or shared), replayed to anyone whose question has the same key; the article links in `episode_sources`; **up to 8 passages per article in `history_archive` intake**, merged across episodes, kept until 500,000 articles (`ARCHIVE_INTAKE_MAX`) |
| **Perplexity** (`research._search_perplexity`, `POST https://api.perplexity.ai/search`) | The query, a result count (≤20), an after-date, an outlet list, a country, a search mode (SEC, academic) | Per result: URL, title, date, a snippet up to `PERPLEXITY_TOKENS_PER_PAGE` (1,024 tokens) | As Parallel - the intake does not record which of the two supplied a passage |
| **GNews** (`gnews.py`, `https://gnews.io/api/v4`) | `top-headlines` by category and country; `search` by a story's words over twelve hours | Articles (title, description, URL, source, date) and `totalArticles` | The edition's stories: headlines (`titles`) and `urls` in the trending bank, plus FAM's composed tile. Never evidence: a tapped tile is researched from scratch |
| **Polymarket** (`live_sources.PolymarketSource`, `https://gamma-api.polymarket.com`, `/public-search`) | The question's subject, a few words | Events and markets: question, outcome prices, volume | Nothing as a record: the forecast is read to the writer as "a forecast, not a result" and credited in provenance; the script that used it is cached like any other |
| **GDELT** (`gdelt.py`, export files) | Nothing a listener does; one job downloads the 15-minute files | Article URLs, titles, dates, themes, tone | A rolling copy on disk; titles and links in packets and intake |

**Findings, for the owner rather than for this file to fix:**

1. **Intake already keeps search passages, and for Parallel's that is what
   the terms forbid.** Today's `history_archive.intake` (§308) stores the
   passages Parallel and Perplexity returned, merged across episodes so a later
   listener's episode can read them, and production keeps the archive in Atlas
   (§339; `ARCHIVE_BACKEND` is set in the dashboard). For Parallel that is
   output "copied, cached, stored, or made available to other End Customers"
   and output "from previous queries for future uses" (P1, P2). For
   Perplexity, nothing in its terms forbids it. Whether to stop intake keeping
   Parallel's words now and delete what is held, or hold them pending email 1,
   is the owner's call.
2. **Intake does not record the provider.** Build-prompt item 8 switches
   storage on per provider; a record that does not say which provider it came
   from cannot be switched off or deleted per provider - and the answers now
   differ by provider. Intake should record `parallel`, `perplexity` or
   `gdelt` beside each passage before anything is deleted or Consuela is
   built; without it, deleting Parallel's passages means deleting Perplexity's
   too.
3. **The shared cache is the open Parallel question.** FAM's design writes
   one script per `key_for` and replays it to every listener who asks the
   same thing (Search DailyFAM's catalogue, Explore, a shared link, the
   Trending and DailyFAM editions written ahead for everyone). The script is
   FAM's words, a "derivative work" P1 permits, but P1's proviso and P2 (xi)
   are written against reuse across End Customers. The terms do not settle
   which way a cached script falls; email 1 asks first about this.
4. **GNews on the Free plan cannot feed production.** G2 makes any use "in
   the course of a business" commercial once "published or put into
   production", and Free-plan data may never be used commercially - even
   after upgrading (G1). The Free plan also delivers articles with a
   "12-hour delay" (G7), which would make the Trending edition half a day old.
   If the production key is on Free, the edition is both late and outside the
   terms; Essential is €49.99 a month for 1,000 requests a day.
5. **What FAM sends is kept by the providers too.** Perplexity "may retain,
   copy, distribute and otherwise use Search Data" - FAM's queries and the
   results (Q2) - and FAM "shall not submit Personal Data" without written
   authorisation (Q5). Parallel may use inputs and outputs "to train and
   improve the models" unless on enterprise zero data retention or the EU
   endpoint (P6). EI's queries come from listeners' questions; whether FAM's
   privacy notice and `consent.py` (a listener's words reach Anthropic only
   after a yes) must cover these two is lawyer item 9.

## The safest shapes, and what each costs

The provider's terms decide whether FAM may keep the *provider's output*; the
publisher's copyright decides whether FAM may keep the *publisher's words*
(next section). A shape is safe only when it passes both.

| Shape | Safe under | What it costs FAM |
|---|---|---|
| **A. Links, titles, dates, outlet and grade only; re-search for the words** | Parallel's terms as far as a link is not "Customer Output" worth protecting (a URL list is still output - see email 1); every other provider; a link and a date are not the publisher's expression (a title may be - see *Copyright*) | Sonic's recall becomes a list of where to look, not what was said: an old figure or quote has to be found again by a new search ($0.001 each, about half a second, and the page may have moved or been paywalled). Trends over years must be rebuilt from figures FAM kept elsewhere (Tiingo, BALLDONTLIE, GDELT) |
| **B. FAM's own one-line digest with the link** (FAM's words, not the provider's) | Usually the safest for copyright; under Parallel it is a "derivative work", which P1 permits for "materials that Customer provides to its End Customers" - but whether it may serve *other* End Customers is the same open question as the cache | **Conflicts with Consuela's rule book**: never 1 says she may never keep her own summary as a fact, and must-always 3 says key sentences are "never reworded". Choosing B means the owner changes those rules on purpose. A paraphrase can also drift from what the source said - the reason the rule exists |
| **C. Keep the words for a limited time** (for example, passages for 30 or 90 days, links and dated figures after) | Perplexity and GNews (no period is required by either); **not Parallel** - its limit is on reuse across listeners, not on time | Recall of last month's reporting works; "what did they say two years ago" does not. Consuela's must-always 4 (consolidate at 90 days) is already halfway there |
| **D. A written agreement or enterprise plan that allows it** | Settles the provider's side, as the owner did with Tiingo and BALLDONTLIE; Parallel names enterprise plans (P6) and contracts by "Online Order" (P1) | Money and a negotiation; does not settle the publishers' copyright |
| **E. Facts as data: the figure, its unit, its date, its link** (a score, a price, a count, a vote total), never the sentence it came in | Facts are not the publisher's expression; under Parallel, a figure lifted from output is still arguably output | Keeps what §313 says is most valuable (scores and prices) - and those mostly come from Tiingo and BALLDONTLIE, which are approved |

**Recommended now, per provider:**

- **Parallel:** A, and intake keeps no Parallel passages (owner's call on
  what is already held). The cache stays as it is pending email 1; if Parallel
  says replays are outside its terms, the choices are an enterprise agreement
  (D) or Perplexity first on the ladder for anything that will be cached.
- **Perplexity:** C or even indefinite, as Perplexity's terms allow - bounded
  by the copyright section, which is now the only limit; short, linked,
  internal.
- **GNews:** a paid plan, titles and links for the edition's life, as today.
- **Polymarket:** the numbers alone, credited as a forecast, nothing stored
  past the episode until email 4 is answered.

## The clauses, with addresses and dates

Every quote below was read on **2026-10-29** from the address given, word for
word (emphasis in the original dropped). Labels (P1, Q1, G1, M1) are what the
table cites.

### Parallel

**https://parallel.ai/customer-terms** (also served as
`https://parallel.ai/customer-terms.md`, same text), "Effective date: October
17, 2026". This is the contract for the API: "By registering an account at
Parallel's online customer platform ("Customer Platform"), accessing or using
the Services, ... you are creating a legally binding and enforceable contract".

- **Definitions, §1:** "(a) "Authorized Users" means Customer's employees,
  consultants, contractors, and agents, and the End Customers, in each case,
  who have been authorized by Customer to access and use the Services under
  the rights granted to Customer pursuant to this Agreement, and for whom
  access to the Services has been purchased hereunder." / "(c) "Customer
  Output" means the output generated and returned by the Services to
  Authorized Users in response to the Customer Input." / "(f) "End Customers"
  means Customer's end customers who have been authorized by Customer to
  access and use the Services as integrated via the Parallel API(s) into the
  Customer's applications, products, or services (each, a "Customer
  Application"), and who have agreed to be bound by terms and conditions at
  least as protective of the Parallel IP as those hereunder."
- **P1 - §2(b) Provision of Access:** "The foregoing license includes the
  right for Customer to use the Parallel API to integrate the Services into the
  Customer Applications and to make Customer Applications available to End
  Customers. ... Customer may modify, adapt, or create derivative works based on
  Customer Output and incorporate Customer Output (and such derivative works)
  into materials that Customer provides to its End Customers, provided that (i)
  Customer Output generated from one query shall be primarily for the use of
  one End Customer only, and shall not be copied, cached, stored, or made
  available to other End Customers or other third parties; (ii) Customer shall
  not copy, cache, or store any significant portion of any Customer Output to
  create the AI and Data Selling Services (as defined in Section 2(c) below)."
- **P2 - §2(c) Use Restrictions:** "Customer shall not, directly or
  indirectly: ... (iv) rent, lease, lend, sell, resell, license, sublicense,
  assign, distribute, publish, transfer, or otherwise make available the
  Services, Software, or Customer Outputs (except as expressly permitted under
  Section 2(b) above); ... (vi) use the Services or any Customer Output to (A)
  create synthetic training data to develop or train a language model or any
  other machine learning model, or (B) create databases, data brokerage, data
  selling/reselling businesses, or related products or services, whether
  competitive with the Services or not (all of the foregoing collectively, the
  "AI and Data Selling Services"), or (C) otherwise for any competitive
  purposes; ... (x) use the Services, Software, or Customer Output in any
  manner or for any purpose that infringes, misappropriates, or otherwise
  violates any intellectual property right or other right of any person ...;
  (xi) intentionally or knowingly diverge requests of Authorized Users away
  from the Services (such as by making available Customer Outputs from previous
  queries for future uses, or incorporating Customer Outputs into an AI and
  Data Selling Services)".
- **P3 - §4(b) Customer IP:** "As between the Parties, Customer retains all
  right, title and interest in and to the Customer IP, except for the license
  granted to Parallel in this Section 4(b). Customer agrees that, and shall
  ensure each End Customer agrees that, each Customer Output shall be primarily
  for the exclusive use of the Authorized User who submitted the corresponding
  query that instructed the Services to generate such Customer Output, and
  shall not be copied, cached, stored, or made available to third parties in
  connection with any AI and Data Selling Services."
- **P4 - §5(a):** "Customer is solely responsible and liable for all uses of
  the Services and Customer Outputs, including all acts and omissions of
  Authorized Users. Customer shall bind all Authorized Users to this
  Agreement's provisions as applicable to such Authorized User's use of the
  Services". §5(b): "Customer Outputs are generated through artificial
  intelligence and machine learning processes and are not tested, verified,
  endorsed or guaranteed to be accurate, complete or current by Parallel."
- No clause names attribution, credit or a link to Parallel; §2(c)(v) only
  forbids removing "any product identification, proprietary, copyright or other
  notices from the Services or Software".

**https://parallel.ai/acceptable-use-policy**, "Effective date: August 11,
2026":
- **P5:** "Transparency : you may not misrepresent the origin of any response
  or output from our AI model or related product by claiming or implying such
  output was created by a human, and you may not remove any markings that are
  automatically applied to an output to indicate that such output was
  artificially generated." Also: you may not "infringe, violate, or
  misappropriate the intellectual property rights, privacy rights, or any other
  rights of any person or entity (including Parallel's rights)".

**https://docs.parallel.ai/resources/faqs.md**:
- "Can I use Parallel for commercial use? Subject to our Terms of Service -
  you own the output you create with Parallel, including the right to reprint,
  sell, and merchandise." (The link is to the Customer Terms; ownership as
  between FAM and Parallel does not lift P1's restriction or give FAM the
  publishers' words.)
- **P6:** "Under the standard Customer Terms, Parallel may use inputs and
  outputs, not linked to any individual, to train and improve the models behind
  its services. Enterprise plans with zero data retention exclude that use.
  Search API traffic sent to the EU endpoint is also excluded."
- Contact: "Email us at partnerships@parallel.ai" (for the SOC report) and
  "Private-cloud and on-prem options are available for qualified enterprise
  customers—ask our team at partnerships@parallel.ai".

**https://parallel.ai/pricing**: the Enterprise box lists "Zero Data
Retention (ZDR)", "Data Protection Agreements", "Custom rate limits", with "Get
a demo" at https://contact.parallel.ai/. No storage or caching term.

**https://parallel.ai/terms-of-service**, "Effective date: August 11, 2026":
governs Parallel's *websites*, not the API (its "Content" is "The materials
displayed or performed or available on or through our website"); it forbids
copying or storing "any significant portion of the Content" of the website. It
does not decide any question here.

### Perplexity

**https://www.perplexity.ai/hub/legal/perplexity-api-terms-of-service**,
"Last updated: January 23, 2026":
- **Q1 - §2.1 Right to Use:** "Customer may, on a non-exclusive,
  non-sublicensable and non-transferable basis during the term of this
  Agreement, ... (ii) use the Services and the API Keys solely to submit Input
  ... to the Service, receive Output ... from the Service, and display such
  Output, in each case, solely within the Customer Applications in accordance
  with the API Documentation".
- §2.3.1 Ownership: "Customer (i) retains all ownership rights in Input and
  (ii) owns all Output." (Overridden for Search by Q3.)
- §2.2: "References to "Customer" herein shall be deemed to include Customer,
  Customer's Authorized Users and end users of the Customer Applications, where
  applicable."
- §2.4 brings the AUP in, including for "end users of the Customer
  Applications". §2.5: Perplexity may switch off a Customer Application it
  finds "competitive with Perplexity".
- §9.2 indemnity for Outputs - which Q3 removes for Search.
- Contact: "please contact Perplexity at api@perplexity.ai"; notices to
  "legal-notices@perplexity.ai" (§11.6).
- No clause on storage, caching, retention period or attribution.

**https://www.perplexity.ai/hub/legal/perplexity-api-terms-of-service-search**
("API Terms of Service - Search Service", the Search API addendum), "Last
updated: September 22, 2025". It "overrides the agreement ... solely with
respect to the particular Perplexity Search services listed on Schedule 1",
which lists `/search`:
- **Q2 - §2.2 Data Use:** "Perplexity may retain, copy, distribute and
  otherwise use Search Data for its lawful business purposes, including the
  improvement and development of products and services. Search Data is not
  Customer's Confidential Information". §1.1.1: ""Search Data" means Input and
  Output."
- **Q5 - §2.3 Personal Data:** "Customer shall not submit Personal Data (as
  defined in the DPA) to the Search Services unless explicitly authorized by
  Perplexity in writing (for clarity, excluding authorizations already included
  in the Agreement)."
- **Q3 - Schedule 1, `/search`:** "An API endpoint that retrieves and returns
  search results (e.g., snippets, URLs, page titles and associated metadata)
  from an index of public content." / "This API does not generate Output
  through the use of artificial intelligence, including large language models.
  The API's Output is sourced from the public internet." / "Customer does not
  own, and Perplexity shall not grant it ownership of, the Search API's
  Output." / "Intellectual property-related indemnification obligations and
  assurances provided by Perplexity under the Agreement with respect to Outputs
  (if any) do not apply to the Output generated by the Search API."
- §1.2.2: Perplexity obligations "(e.g., update, upgrade and/or zero data
  retention obligations) do not apply to the Search Services."

**https://docs.perplexity.ai/docs/search/overview.md** (Search API
Overview):
- **Q4:** "You send a query and get back ranked web results as structured data
  for your own code to filter, store, or hand to a model." and, in its table,
  "Ranked results your code filters, stores, or feeds to your own model |
  Search API".

**https://docs.perplexity.ai/docs/search/best-practices.md**: "2. Cache
frequently used searches - Implement caching for queries that don't need
real-time results."

**https://www.perplexity.ai/hub/legal/aup**, "Last updated: July 8th, 2025":
- **Q6:** you may not use the Services to "Create content that you
  subsequently claim was created solely by humans, or otherwise misrepresent
  the provenance of information or content; or provide chatbot services without
  disclosing to end users that they are interacting with AI (unless it is
  obvious from the context)"; nor to "Submit any material to the Services, or
  use the Services to create, compile or distribute any material, without
  having all necessary rights, licenses, consents or permissions to do so"; nor
  "Create or make available any products or services competitive with, similar
  to, or that would otherwise be a substitute for the Services ... except with
  Perplexity's express prior written permission".

**https://docs.perplexity.ai/docs/getting-started/pricing.md** and
**https://docs.perplexity.ai/docs/admin/rate-limits-usage-tiers.md**: the
Search API is billed "per successful request" and limited to "50 query units
per second", "independent of your usage tier". No storage term.

### GNews

**https://gnews.io/legal/terms-of-service** (the address `gnews.io/llms.txt`
names; `https://gnews.io/terms` returns 404), "Last Updated: October 18,
2026":
- **G1 - §3.1:** "You may use the API for commercial purposes only while you
  hold an active paid subscription. ... If you do not hold an active paid
  subscription (including while on the Free plan or on any plan provided to you
  free of charge, such as education access), your license to use the API is
  limited to personal, academic, evaluation, and other non-commercial use."
- **G2 - §3.3 Commercial Use of the API and API Data:** "An active paid
  subscription plan is mandatory for as long as you use the API for a
  commercial purpose. Commercial purposes include, without limitation, use in
  any product, application, service, or project that is offered for sale,
  generates revenue, or is otherwise used in the course of a business or for
  commercial advantage. Developing, testing, and evaluating an integration that
  has not been published or put into production is not a commercial purpose."
  / "If you do not hold an active paid subscription, including while using the
  Free plan ..., you may use the API, and the data you retrieve through it
  during that time, for personal, academic, evaluation, and other non-commercial
  purposes only, and you may not use them for any commercial purpose."
- **G3 - §3.3:** "If your paid subscription lapses, is cancelled, expires, or
  otherwise ends, your commercial project may keep using the data you retrieved
  while your paid subscription was active, including data cached or stored, and
  you are not required to delete it, unless we terminate your account for cause
  as described in Section 8.4."
- **G5 - §3.3:** "A paid subscription does not grant you any rights in
  third-party content, which remains the property of its respective owners" /
  "You must comply with all applicable copyright laws and respect third-party
  intellectual property rights" / "Images and media content obtained through
  the API may be subject to copyright protection by third parties; you are
  solely responsible for ensuring you have the necessary rights to use such
  content" / "You must not represent that API data originated from you or
  misrepresent the source of the data" / "Attribution to GNews API is
  appreciated but not required unless specified in your subscription plan".
- §3.2: you will not "Redistribute, resell, or sublicense access to the API
  without our express written consent" or "Use the API to create a competing
  service or product".
- **G4 - §7 Prohibited Uses:** "To create a database by systematically
  downloading and storing all or substantial portions of API content for the
  purpose of creating a competing service, redistributing the data, or avoiding
  API usage limits. Reasonable caching of API responses for application
  performance is permitted."
- §5.2 and §6.2 Third-Party Content: "News articles, images, and other content
  accessible through the API are sourced from third parties and remain the
  property of their respective owners." / "No permission to use this content
  for commercial purposes is granted to you except as described in Section 3,
  and any such permission may be revoked as described in Section 8.4." / "You
  are responsible for ensuring compliance with copyright and intellectual
  property laws when using content obtained through the API".
- **G6 - §8.4:** if the account is terminated "for breach of these Terms or for
  fraudulent, abusive, or illegal activity, any permission to use data
  retrieved through the Service is revoked, and you must stop all use of that
  data and permanently delete it, including any data previously retrieved,
  cached, or stored".
- Contact: contact@gnews.io (named in the terms for refunds and service
  credits, and on the home page).

**https://gnews.io/pricing** and **https://gnews.io/faq**:
- **G7:** Free: "For development, testing and non-commercial projects", "Non-
  commercial use only", "100 requests per day", "12-hour delay". Essential
  "€49.99 /month": "For production and published commercial projects",
  "Commercial use allowed", "1,000 requests per day", "Real-time article
  availability".
- FAQ: "Can I use the Free plan for a commercial project? No. A commercial
  project needs an active paid plan for as long as it uses GNews API." / "Can I
  use the articles in a commercial product? Yes, with an active paid plan ...
  You remain responsible for respecting third-party copyright: article text and
  images belong to their publishers, and you must not present the data as your
  own."

### Polymarket

**https://polymarket.com/tos** ("Terms of Use"; `/terms-of-use` returns 404),
"Effective as of August 11, 2026":
- **M1:** "BY ACCESSING, INTERACTING WITH OR USING THE SITE, ANY FEATURE OR ANY
  OTHER INTERFACE (INCLUDING BY CONNECTING VIA AN API, LINKING YOUR WALLET, OR
  OTHERWISE CREATING AN IDENTIFIER ON THE SITE), ... YOU ... AGREE TO BE BOUND BY
  THE TERMS". "The Site primarily functions to provide the Content Features —
  that is, news and information about global current events."
- **M2:** "Subject to the Terms, the Company hereby grants you a personal,
  limited, revocable, non-exclusive, non-sublicensable, non-transferable license
  to use, copy, and distribute in connection with such use the Site, any other
  Interface, and Features. This license is solely intended to allow you to
  access, use or otherwise interact with the Site, any other Interface, and
  Features."
- **M3:** you may not "Access or use any data, content, or information
  contained on our Site, any Interface or Features directly or through an API,
  or any other means, including on-chain, whether in raw, derived, aggregated,
  or anonymized form (the "Data") if you are (i) a non-retail, professional
  entity that engages in capital markets activities (e.g., brokerage, market
  making, proprietary trading, index calculation, or ETF issuance), including
  but not limited to broker-dealers, futures commission merchants, registered
  investment advisors, hedge funds, investment managers, proprietary trading
  firms, commodity pool operators, financial technology companies, banks,
  clearing houses, and exchanges or an affiliate of any such entity (each, a
  "Capital Market Client"), or (ii) a market data distributor, in each case
  unless otherwise agreed to in writing by us;" nor "Sell, resell, sublicense,
  redistribute, or otherwise commercially exploit the Data to any Capital
  Market Client or market data distributor, unless otherwise agreed to in
  writing by us"; nor "Use any data mining tools, robots, crawlers, or similar
  data gathering and extraction tools to scrape or otherwise remove data from
  the Site, any other Interface, or Features".
- "You acknowledge and agree that all information provided as part of the
  Content Features in connection with your access and use of the Site is
  intended for informational purposes only." Restricted Jurisdictions bar
  people there from trading; "you are only permitted to use the Content
  Features".
- No clause on storing Data, a retention period, quoting odds, or attribution.
- Contact: "You may also contact us with questions, complaints, or claims
  concerning the Features at hello@polymarket.com."

**https://polymarket.com/brand**:
- **M4:** "The Polymarket wordmark and logo should be used in all references
  to Polymarket as space allows." (A brand guideline, not a licence term; an
  episode has no space for a logo, the player's sources list might.)

**https://docs.polymarket.com/api-reference/rate-limits.md**: Gamma API,
"Base URL: `https://gamma-api.polymarket.com`", "`/public-search` | 350 req /
10s". No terms of use on the docs pages read
(`https://docs.polymarket.com/llms.txt`, `/market-data/overview.md`,
`/api-reference/geoblock.md` - the last is about blocking *order placement*).

### Pages that did not open (2026-10-29)

- https://www.perplexity.ai/hub/legal/search-api-addendum and
  https://www.perplexity.ai/hub/legal/api-terms-of-service - HTTP 404 (the
  addendum lives at `.../perplexity-api-terms-of-service-search`, above)
- https://gnews.io/terms - HTTP 404 (the terms live at
  `/legal/terms-of-service`, above)
- https://docs.gnews.io/ - `CONNECT tunnel failed, response 403` (outside this
  session's allowed domains). `gnews.io/llms.txt` points at it for
  documentation only, not terms
- https://polymarket.com/terms-of-use, https://polymarket.com/api-terms,
  https://docs.polymarket.com/api-usage-policy - HTTP 404

### The first pass's leads, checked

The first pass (§341) listed leads from search results and the agents guide's
cached copy. Each against the live page:

- Parallel, "Customer Output" defined as what the Services return to
  Authorized Users - **confirmed**, §1(c), quoted above.
- Parallel, output "primarily for the exclusive use of the Authorized User who
  submitted the corresponding query" - **confirmed**, §4(b) (P3).
- Parallel, a restriction on "storing Output for other End Customers" -
  **confirmed in other words**, §2(b)(i) (P1): "shall not be copied, cached,
  stored, or made available to other End Customers".
- Parallel FAQ, the customer owns the output - **confirmed**, quoted above.
- Parallel, enterprise zero data retention - **confirmed** (P6 and the
  pricing page).
- ~~Parallel, a Google Cloud Marketplace agreement~~ - **struck**: not on the
  Customer Terms, FAQ or pricing page read.
- Perplexity, the Search API addendum says the customer does not own Search
  API output - **confirmed**, Schedule 1 (Q3).
- GNews, the free plan "for non-commercial projects or for development and
  testing purposes only", paid plans from about €49.99 - **confirmed in
  substance, not in those words**: the live wording is "For development,
  testing and non-commercial projects" (G7); Essential is €49.99 a month.
- GNews, `llms.txt` points at `/pricing` and `/terms` - **corrected**: it
  points at `/pricing` and `/legal/terms-of-service`.
- ~~Polymarket, the participant agreement refers to "the Polymarket Terms of
  Service and API Usage Policy"~~ - **struck**: the Polymarket US
  participant agreement
  (https://www.polymarketexchange.com/files/legal/latest/participant-agreement,
  a PDF) does not contain "API Usage Policy", and
  `docs.polymarket.com/api-usage-policy` is a 404. That agreement binds
  Polymarket US account holders (FAM is not one); its "All data and
  information available via the System is proprietary to PMUS ... may not be
  redistributed except as PMUS expressly permits" is about that exchange, not
  the public Gamma API.

Settled providers were not re-read: GDELT's terms (cite and link), and the
owner's agreements with Tiingo and BALLDONTLIE, stand as the owner recorded
them on 2026-10-28.

## The publishers' copyright, separately

A provider's terms and a publisher's copyright are two different permissions.
Parallel or Perplexity can only license what they own; the words in a search
excerpt were written by the outlet that published them, **whatever the
provider allows**. The providers say so themselves:

- **Perplexity:** "Customer does not own, and Perplexity shall not grant it
  ownership of, the Search API's Output", which is "sourced from the public
  internet", and its "Intellectual property-related indemnification obligations and assurances ... do not apply" to it (Q3).
- **GNews:** "A paid subscription does not grant you any rights in third-party
  content, which remains the property of its respective owners"; "article
  text and images belong to their publishers" (G5, FAQ); "Images and media
  content ... may be subject to copyright protection by third parties".
- **Parallel:** ownership of output is FAM's "as between the Parties" (§4(b),
  FAQ) - which says nothing about the publishers - and FAM may not use output
  in a way that "infringes ... any intellectual property right ... of any
  person" (P2 (x)); FAM indemnifies Parallel for claims arising from FAM's use
  (§5(b)).
- **Polymarket:** odds are Polymarket's own data, not a publisher's; the
  question is Polymarket's licence (M2), not copyright.

So for Perplexity especially, where the provider's terms no longer stand in
the way, the copyright points below are the whole answer. What that means for
the shape Consuela's rule book asks for - key sentences kept verbatim, with a
link, indefinitely:

- **Speaking an episode** that paraphrases and credits what was reported is
  the lowest-risk use: FAM's script is FAM's words, and facts themselves are
  not anyone's expression.
- **Keeping short verbatim sentences** is a copy of the publisher's words,
  made for a commercial product and kept for years. Short, attributed,
  linked and internal lowers the risk; it does not remove it. Scale matters:
  500,000 articles' passages is a database of other people's words, not a
  handful of quotes - and under Parallel a database built from output is
  barred outright (P2 (vi)(B)).
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
   With Perplexity's terms silent, this decides Perplexity's passages.
2. Whether the EU press publishers' right (Article 15 of the DSM Directive)
   and the UK and EU text-and-data-mining exceptions reach FAM if it has
   European listeners or stores data in Europe, and whether headlines and
   "very short extracts" are inside them.
3. Whether a FAM-written one-line digest (shape B) is treated as a
   derivative work of the article, and whether that matters for internal use.
4. Whether each provider's restriction on storing or reusing Output survives
   the publisher's own permission (for instance, an outlet whose site allows
   copying), and whether facts extracted from Output are still "Output" - for
   Parallel, whether a URL, a date or a figure is "Customer Output" under P1.
5. Whether credit in the episode ("according to Reuters") and the sources list
   on the player are enough attribution under the publishers' expectations
   (no provider here requires its own credit), and whether GDELT's citation
   needs to appear in the app, the episode, or the Terms.
6. Whether FAM's Terms of Service and Privacy Policy must say that FAM keeps
   an archive of third-party reporting, and how a publisher asks for removal
   (a takedown address and process).
7. Whether a removal request from a publisher must reach the cached scripts
   and kept audio that paraphrase it, or only the archive's stored words.
8. Whether the passages already in Atlas from intake (*Findings* 1 and 2)
   should be deleted, kept, or kept pending Parallel's answer - and, since
   intake does not record the provider, whether deleting them means deleting
   Perplexity's too.
9. Whether FAM's Terms bind listeners "to terms and conditions at least as
   protective of the Parallel IP" (§1(f)) as Parallel requires of End
   Customers, and whether its privacy notice must say that EI's queries,
   drawn from listeners' questions, go to Parallel (which may train on them,
   P6) and Perplexity (which may "retain, copy, distribute and otherwise use"
   them, Q2) - and whether a question naming a private person is "Personal
   Data" FAM may not send Perplexity (Q5).

## Draft emails (not sent)

From Admin@familiarize.net, sent by the owner. Each address below is the one
the provider's own site gives (quoted under *The clauses*). **GNews needs no
email**: its terms answer every question (G1-G6); what is left is a
subscription, not a question.

### 1. Parallel

> **To:** partnerships@parallel.ai (the address Parallel's FAQ gives for
> enterprise customers)
> **Subject:** Customer Terms §2(b)(i) - cached episodes, and an enterprise agreement
>
> Hello,
>
> We run FAM, a consumer app that turns news questions into short spoken
> episodes. We use the Search API (`/v1/search`) with our own API key: for a
> listener's question we search, read the results, and write an episode in
> our own words that credits the outlets.
>
> Your Customer Terms (effective October 17, 2026) say in §2(b) that we "may
> modify, adapt, or create derivative works based on Customer Output and
> incorporate Customer Output (and such derivative works) into materials that
> Customer provides to its End Customers, provided that (i) Customer Output
> generated from one query shall be primarily for the use of one End Customer
> only, and shall not be copied, cached, stored, or made available to other
> End Customers". We read that as ruling out keeping your excerpts to write a
> different listener's episode, and we will stop doing that. Two things it
> does not settle for us:
>
> 1. When two listeners ask the same question, we write the episode once and
>    play the same script (our own words, not your excerpts) to both. Is a
>    script we wrote from Customer Output - a derivative work - covered by
>    the proviso in §2(b)(i), or only the Customer Output itself? And does
>    "primarily" allow a script written for one listener to be replayed to
>    others who ask the same thing?
> 2. Twice a day we write about ten trending stories ahead for all our
>    listeners. Is a search made before any listener has asked within the
>    terms at all?
>
> If either is outside the standard terms, what agreement or plan would allow
> (a) replaying our scripts to other listeners and (b) keeping short excerpts
> with their links for later episodes - and what would it cost?
>
> Thank you,
> [Owner's name], FAM - Admin@familiarize.net

### 2. Perplexity

> **To:** api@perplexity.ai (the address in the API Terms and the Search
> addendum)
> **Subject:** Search API addendum §2.3 and API Terms §2.1 - two questions
>
> Hello,
>
> We run FAM, a consumer app that turns news questions into short spoken
> episodes, and use the Search API (`/search`) as a second search. We have
> read the API Terms (January 23, 2026) and the Search addendum (September
> 22, 2025); two points are not clear to us:
>
> 1. Addendum §2.3 says "Customer shall not submit Personal Data (as defined
>    in the DPA) to the Search Services unless explicitly authorized by
>    Perplexity in writing". Our queries are short keyword searches drawn from
>    listeners' news questions, and often name public figures (a politician,
>    an athlete). Is that Personal Data in your sense, and if so, would you
>    authorise it in writing?
> 2. API Terms §2.1(ii) let us "display such Output, in each case, solely
>    within the Customer Applications". We never show results verbatim: we
>    write a spoken episode in our own words from them, played in our app and
>    on a share page on our own website. Is that within §2.1(ii)?
>
> Thank you,
> [Owner's name], FAM - Admin@familiarize.net

### 3. Polymarket

The address: hello@polymarket.com, the one Polymarket's Terms of Use give for
"questions, complaints, or claims concerning the Features", and in its site
footer. `builder@polymarket.com` in its docs is for trading-builder API keys
and rate limits, not data licences; `press@polymarket.com` is press.

> **To:** hello@polymarket.com
> **From:** Admin@familiarize.net
> **Subject:** Request for written permission to quote and store Polymarket odds in a news app
>
> Hello,
>
> I run FAM (familiarize.net), a consumer app that turns news questions into
> short spoken audio episodes. I'm writing to ask for written permission to
> use Polymarket market data in FAM as described below. If you'd rather this
> went through a licence or agreement, please tell me who to speak to.
>
> **How we use Polymarket today**
>
> We read your public Gamma API (`gamma-api.polymarket.com`) with no account
> or key. We use it three ways:
>
> 1. **Beside an episode.** When a listener asks about something with an open
>    outcome, such as an election, a court ruling or a championship, we look
>    the subject up with `/public-search`. The episode may then say something
>    like: "Polymarket traders give this a 60% chance. That's a forecast, not a
>    result." Polymarket is always named as the source, and the odds are never
>    presented as an outcome.
> 2. **To spot stories.** About every two hours we read the most-traded open
>    markets (`/markets`, ordered by 24-hour volume). That helps us notice
>    which questions people are following. Those stories are then researched
>    from news reporting, not from Polymarket.
> 3. **Not stored today.** We don't keep the odds we read. An episode that
>    quoted them is cached and replayed to other listeners who ask the same
>    question.
>
> We don't trade, route orders, take bets or resell data. We aren't a broker,
> an exchange, an investment adviser or any other capital-markets business.
> Our listeners can't trade through FAM.
>
> **Why I'm asking**
>
> Your Terms of Use (effective August 11, 2026) cover API use. They grant "a
> personal, limited, revocable, non-exclusive, non-sublicensable,
> non-transferable license", and they bar access by a "Capital Market Client"
> or "a market data distributor" "unless otherwise agreed to in writing by
> us". They don't say whether a commercial news product may quote your odds to
> its audience. I'd like that settled in writing rather than assumed.
>
> **What I'm asking permission for**
>
> 1. To read the public Gamma API in a commercial app, and to say current
>    odds aloud in episodes heard by the public, credited to Polymarket. This
>    includes episodes we cache and replay to other listeners.
> 2. To use your most-traded markets as a signal for which stories to cover.
> 3. To store the odds we read (market, outcome, price and time), so an
>    episode can say how a forecast changed over time. If there's a limit on
>    how long we may keep them, please tell me.
> 4. Your preferred credit wording, and whether you'd like a link to the
>    market wherever we can show one, for example in an episode's source list.
>
> Please also confirm that this use doesn't make FAM a "market data
> distributor" under your Terms. If any of it needs a data licence or a fee,
> I'd be glad to hear the terms.
>
> Thank you,
>
> [Your name]
> FAM: Admin@familiarize.net
> [Phone, optional]

Before sending: their Terms open this only "in writing", so a reply that
just says "go ahead" may not be enough - if that is what comes back, ask for
points 1-4 confirmed in an email from a named person. If they want usage
figures, give the real counts from `/admin`; the draft states none. Whether
Polymarket shows in the player's visible source list today was not checked,
which is why point 4 says "wherever we can show one".

## To finish this

Nothing is left to read: every page the request named was opened on
2026-10-29 or is listed above with its error. What remains is the owner's:

1. Decide on intake's Parallel passages (keep pending email 1, or stop and
   delete) - and have intake record the provider first (*Findings* 2).
2. Send emails 1, 2 and 3. A provider's written reply outranks its page, and
   goes here quoted the same way, with the table re-marked.
3. Check which GNews plan the production key is on; if it is Free, subscribe
   to Essential.
4. Take the lawyer list to a lawyer.
