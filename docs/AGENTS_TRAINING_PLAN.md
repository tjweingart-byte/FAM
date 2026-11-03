# Training Consuela and Sonic - a plan

**Status: C1 and S1 built (§407); the C3 and S4 rules proposed, waiting for the owner's signature (§418); the rest is a plan for the owner.** Written 2026-11-02 at the
owner's request, after Phase 2 built Consuela (the housekeeper, §399-§402)
and Sonic (the retriever, §403). PROBLEMS.md §406 records it. Each piece
below says what it needs from the owner before it starts.

## The idea in one paragraph

Nando already has a training loop (§381-§387): a stronger model grades his
work nightly, the grades are remembered and recalled by meaning, his
corrected mistakes become worked examples, and a small model of his own is
trusted only when its numbers say so. Consuela and Sonic can learn the same
way, under the same rules: **nobody hand-labels anything** (decided item
30), **grades teach and measure, they never change the archive or an
episode by themselves**, **nothing about a listener is used**, and **every
change in behaviour is measured before it is trusted**.

## What exists to build on

| Already built | Where | Use in training |
|---|---|---|
| Nando's answer key, memory, examples, small model | `agents/training.py`, `agents/memory.py`, `category_examples.py`, `agents/nando_model.py` (§381-§387) | The pattern and much of the code |
| Consuela's dedupe eval: 21 pairs built in code; passed with Haiku and Sonnet, 0 wrong merges | `agents/dedupe_eval.py` (§401) | The seed of a larger eval |
| Consuela's report of every choice and merge, dry run included | `housekeeper.db` (`consolidations`, `merges`, `evals`) | Material for new eval cases and examples |
| Sonic's counts: lookups, who chose them, empties, fallbacks | `agents/retriever.py` `STATS`, `/api/health` | The first measures |
| The task table and nightly schedule | `agents/tasks.py`, `agents/netenyahu.py` | Where the training jobs run |

## What must be true first

- **Consuela consolidating for real** (`HOUSEKEEPER_DRY_RUN=0` on a
  deployment). Sonic's training needs filed facts, and there are none until
  she writes them. Consuela's own first pieces (C1, C2) can start from her
  dry run's report.
- **The models set for each agent**: grading uses a stronger model than the
  one graded. Nando's precedent is Opus 5.5. Every model named here is a
  proposal; the owner decides (decided item 28).

## The pieces, in order

Each piece ends like every other: `./dev.sh check`, a PROBLEMS.md section,
a short summary. Everything is off by default.

### Consuela

**C1. A dedupe eval that grows by itself.** *First, because it is what
lets her dedupe leave its dry run.* **Built (§407)**: `agents/dedupe_cases.py`;
`tools/consuela_dedupe_eval.py` runs the core and the grown cases.
- Code makes new never-duplicates from real material, taken from her report
  and the archive: the same sentence with a number changed, a date moved, a
  hedge added ("won" → "is expected to win"), a speaker swapped, a subject
  swapped for another of the same kind. Each is a pair whose right answer
  is known because code made it.
- Every pair where Haiku and Sonnet disagreed in a night's dedupe becomes a
  new case, with its answer settled the safe way (kept apart) until a grade
  (C3) says otherwise.
- The eval keeps its hand-written core (the guide's never-duplicates table)
  and grows around it; it still passes only with no wrong merge.
- **Needs from the owner:** nothing. **Cost:** the eval's own calls when it
  runs - a few cents a run at today's size.

**C2. Thresholds measured, not guessed.** The dedupe's similarity cut-offs
(0.80 to be looked at, 0.90 for code to call two figures one fact) and the
hedge word list are tuned on C1's eval. Each proposed change is shown to the
owner as before and after: wrong merges, missed repeats, pairs sent to a
model.
- **Needs from the owner:** a yes to each change. **Cost:** none beyond C1.

**C3. An answer key for her choices.** Nightly, in the background, a
stronger model grades a sample of her work: for consolidation, whether the
sentences she kept are the article's key ones and whether each figure's
"happened"/"expected" is right; for dedupe, whether a pair she kept apart or
merged was right. Grades are stored and only teach and measure her - they
never re-file, merge or restore anything.
- **Needs from the owner:** a **signed rule** in `consuela.md`, like
  `nando.md` rule 12 (§384) - a proposal file first, as usual; the
  grading model (proposed: Opus 5.5); how many batches a night.
- **Cost, estimated:** a consolidation batch is ~10,000 tokens in and ~300
  out; graded by Opus 5.5 ($4 / $20 per million) that is ~5 cents a batch.
  Ten batches a night is ~$15 a month; twenty is ~$30.

**C4. Her mistakes become worked examples.** As §383 did for Nando: a choice
the answer key corrected becomes a short worked example (the article's
numbered sentences, the right numbers) in her cached instructions, kept
fresh and capped in size; graded answers are remembered and the nearest
ones recalled by meaning when she sees a similar article (§382's memory).
- **Needs from the owner:** C3 running. **Cost:** a slightly longer cached
  prompt; cached tokens are cheap.

**C5. Learning from use.** Count how often each kept fact is brought back by
Sonic, and later (Phase 3) how often the writer relies on it, as Mclovin
sees. Facts never used after months, by kind and by node, show what "key"
really means; the report goes to the owner, and its clearest cases become
C4 examples.
- **Needs from the owner:** nothing to start; Phase 3 for the writer's use.
  **Cost:** none - counts, no model. Nothing about any listener is kept.

**C6. Later, only if the numbers ask for it.** A small model of her own for
the first pick, or a self-hosted "same claim?" checker (an NLI model) as a
free first look in the dedupe - each trusted only when it beats today's
setup on the eval, as §385 did for Nando.
- **Needs from the owner:** a yes to any new dependency or machine.

### Sonic

**S1. A recall eval built from the archive itself.** *First, because nothing
about Sonic is measured yet.* **Built (§407)**: `agents/retriever_eval.py`,
`tools/sonic_recall_eval.py`. Code takes a stored fact and writes the
question it answers from what is stored with it - its subject, its node, its
date ("what happened with the Eagles in February 2025") - then checks that
Sonic brings it back, in date order, without facts from the wrong node, and
that nothing comes back from inside the search's window. Results and series
are checked the same way from the live readings.
- Measures: found in the packet, wrong-node items, order, empties, time.
- **Needs from the owner:** Consuela writing facts (above). **Cost:** none -
  no model, Sonic makes no model call.

**S2. The router learns from C3P0.** Whenever C3P0 names a lookup for a
question, that question and lookup join the router's example bank (questions
only - the request is already C3P0's material, and a private search never
reaches Sonic). The router's agreement with C3P0 is counted; when it agrees
often enough, the episodes C3P0 does not cover get a better router.
- **Needs from the owner:** a yes that searched questions (never private
  ones) may be kept as router examples - they are listeners' words, so this
  is the owner's call, and `consent.py` must cover it. **Cost:** none.

**S3. Tuning from measurement.** On S1's eval and the live counts: how few
facts make a node "too thin" (today 2), the router's floor (0.55), the
archive's similarity floor (0.45), and how often the 0.5 s grace window is
missed. Each change shown as before and after.
- **Needs from the owner:** a yes to each change. **Cost:** none.

**S4. An answer key for his packets.** Nightly, a stronger model grades a
sample of finished episodes' history packets: was each item relevant to the
question, was anything missing that the archive held (from S1's
knowledge of what is stored). Grades teach and measure only.
- **Needs from the owner:** a **signed rule** in `sonic.md`, as for C3; the
  grading model; the nightly sample size. The grader reads the episode's
  question, a listener's words, so only Anthropic (covered by `consent.py`)
  and never a private search, as for Nando's answer key.
- **Cost, estimated:** ~1,500 tokens in and ~150 out per episode, ~1 cent on
  Opus 5.5; fifty a night is ~$15 a month.

**S5. What the writer used** (Phase 3). Once Mclovin checks each sentence
against the packets, count which archive items the script relied on, per
lookup and per node: the most direct measure of whether Sonic brought the
right history.
- **Needs from the owner:** Phase 3. **Cost:** none.

**S6. Later, only if order matters.** Keyword search beside vectors (Atlas
Search; check the cluster tier) or a self-hosted reranker, each trusted only
when it beats today's lookups on S1's eval.
- **Needs from the owner:** a cost or dependency decision for either.

## Suggested order

1. **C1** and **S1**: measures first, no model cost and no rule change.
2. **C2** and **S3**: tuning on those measures, each change shown first.
3. **C3** and **S4**: the answer keys, once the owner has signed the two rules.
4. **C4**, **S2**, **C5**: learning from grades and from use.
5. **S5** with Phase 3; **C6** and **S6** only if the numbers ask for them.

## Decisions for the owner

1. Whether to start with C1 and S1 (no cost, no rule change).
2. The two answer-key rules (`consuela.md`, `sonic.md`), to be proposed as
   files and signed, as `nando.md` rule 12 was.
3. The grading model for each (proposed: Opus 5.5) and how much a night.
4. Whether searched questions may become Sonic's router examples (S2).
5. Any later dependency or machine (C6, S6).

## What this plan does not change

No rule book's meaning (changes go through proposals and the owner's
signature); `AGENTS=0` changes nothing; staging spends nothing; nothing
about who a listener is is kept; a grade never changes the archive or an
episode by itself.
