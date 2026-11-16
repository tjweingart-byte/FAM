---
name: fam-method
description: How to work on FAM step by step - find the rule and its history, find the tool that already exists, diagnose a weak episode, add writing examples and before-and-after pairs, verify before claiming, spend the owner's key carefully, and ship. Use for any non-trivial change to this repo, any work on the writer or examples/, and before reporting that something is done.
---

# How to work on FAM

This is method, not rules. The rules are in `CLAUDE.md` and `docs/claude/`,
and a rule always wins over this file. Rules are cited here by ID, never
restated: `grep -n 'rule:ID' docs/claude/*.md` gives the full text. It was
written at the end of a session that did the work in section 5 (PROBLEMS.md
§619, §620); the examples below are what happened, not advice in general.
A session on another model then followed it on a real task and corrected
three things in it (§621); those corrections are in the text.

## 1. Before you change anything

1. **Read the full text of every rule the change touches**, not its line in
   `CLAUDE.md`. The line for `[examples-lever]` says to add an example over
   a rule. The full text, `examples/README.md` and PROBLEMS.md §527 add:
   prefer replacing a weak example to adding one, and naming matched
   examples in the request made repetition worse and is switched off. The
   one-line version alone leads to the wrong change.
2. **Read the history by offset.** Find the section in `PROBLEMS_INDEX.md`,
   then read `PROBLEMS.md` from that line. Look for "The owner": a decision
   the owner already made is not yours to make again.
3. **Look for the tool before building one.** This repo usually has it.
   `ls tools | grep -i <word>`, then read the tool's docstring: it says what
   it reads, what it writes, what it costs and what it refuses to do.
4. **Check the machine, not the document about the machine.** `[no-api-key]`
   says there is no key here; the environment may hold `FAM_ANTHROPIC_KEY`
   and the search keys (`[ -n "$FAM_ANTHROPIC_KEY" ] && echo set`). Never
   print a key. `[ci-python]` names the container's Python; run
   `python3 --version`, because the container has moved since.
5. **Check CI on `Main`** before starting (`[ci-green-first]`).

## 2. Working out what is being asked

The owner writes short, often from a phone, and often points at something
outside the repo. Translate the request into the repo's own terms and say
the translation back in one or two lines before doing work that costs
money or changes what a listener hears.

- If the request is about something you cannot read (a link, a file), say
  so in the first line and get it read. Never assess what you have not seen.
- If it would change what every episode is written from, the first version
  goes somewhere nothing loads, for the owner to read. Move it in when the
  owner says to. They did, the same day.
- Ask only when the answer changes what you do next: spending on the
  owner's key, or anything `CLAUDE.md` files under "discuss before undoing".
  For everything else pick the sensible default and say which you picked.
- Push back when a request runs into a rule. Name the rule ID and say what
  would have to change; do not quietly build around it.

## 3. Diagnosing a weak episode

Brief and script fail separately and are fixed in different files
(`[brief-above-script]`). Suspect them in this order (`[inputs-not-prompt]`):

1. what was searched - the brief (`python write.py "<query>" --minutes 3`
   prints it above the script; `--no-ei` compares);
2. what the packet held - dates, grades, and whether two sources disagree;
3. what the minutes were for (`DEPTH_BANDS`);
4. only then the writer's instructions and `examples/`.

Read a draft against its packet line by line: every name, number, date and
attribution. In 36 real drafts the failures were of these kinds:

- speaking about its own material ("the latest price we have", "I don't
  have a confirmed result") although the instructions already forbid it;
- one outlet's headline credited to somebody else;
- "have not won since" read as one win where the packet has two;
- a two-sentence recap as the ending, which the code does not flag;
- a packet in which one source reports a result and the others still
  preview it. `[started-not-finished]` decides that. Never teach it the
  other way in an example.

A failure the instructions already forbid is not fixed by forbidding it
again. **Find the check that already exists for it and ask why it missed.**
`OpeningGuard` and `MaterialGuard` in `script_generator.py` drop sentences
about the writer's own material on every episode, and each was narrowed
after it ate real reporting (§94, §288). What a frozen draft holds is what
got past them; what they dropped is not saved in it, so a count from drafts
is of leaks, never of attempts. Only after that are the levers an example,
a pair, or a wider check with the old false positives kept as tests.

## 4. Verify, do not inspect

- Run the thing (`[verify-not-inspect]`). A check you read is not a check
  you ran.
- Say which numbers were measured and which are estimates. A number in a
  report belongs to the final text: if you edit after grading, grade again.
- A proxy is a proxy. `episode_quality.measure` flagged `visual` on the film
  title "You Can See Everything". Report the false alarm; do not bend the
  sentence to clear it.
- Check your own facts hardest where they sound best. A draft example said
  the measles vaccine came decades before any antiviral drug. Both date
  from 1963. Any "first", "before" or "decades" is checked or cut.
- A count from one pattern is a lower bound. This file first said three of
  36 drafts spoke about their own material. The session that tried it
  counted again with wider patterns: four drafts, seven sentences.
- End a log entry with what was not verified, as PROBLEMS.md does.

## 5. Changing the writing: examples and pairs

An example teaches voice, length and shape (`examples/README.md`). A pair
teaches by a real mistake fixed (`examples/pairs/README.md`). The procedure
that produced the six pairs:

1. `AGENTS=1 EDITOR_AGENT=1 python tools/editor_eval.py --freeze --only
   <numbers> --dir ~/.fam/<run>` researches fixed questions once. Packets
   hold provider text and stay in `~/.fam/`, never in the repository.
2. `--run --arms none,none_low --no-grade` has the production writer draft
   each. Ungraded first: grading is most of the cost.
3. `tools/teaching_pairs.py`'s `candidates()` ranks drafts by the flaws the
   code names. Pick across facets, not the top six of one kind.
4. Rewrite from the **same packet**, saying only what it holds, no longer
   than the draft. Say the cause aloud (because, so, but, yet): the flow
   measure counts them, and so does a listener. One figure to a sentence.
5. Gate it as the tool does: fewer flaws by the code, no hook, story, flow
   or ear score lower, then the grader's mean of six at least 0.3 higher
   with no more unsupported claims. Set `QUALITY_GRADER_MODEL` so the
   grader is stronger than the writer.
6. Take out whatever the grader calls unsupported in your own rewrite, and
   grade that pair again.
7. The writer is shown at most six pairs, in file-name order. A seventh
   file is never read.

For an example: settled facts only, never a current figure; fill a gap in
length or shape (`docs/story_shapes.md` lists which shapes have none); name
its facet in `examples/facets.json`; write its worked plan and run
`python tools/editor_teaching.py --check`.

Whether a change to `examples/` moves the writing is a measurement
(`tools/editor_eval.py`, arms with and without, as §527), not a feeling.
Say when it has not been measured.

## 6. Spending the owner's key

- Ask once, with an estimate. Report the actual afterwards, split into
  measured and estimated.
- Take the cheapest run that answers the question, and the smallest set of
  questions that covers the facets.
- Nothing is spent on staging (`[zero-spend-staging]`), and
  `tools/teaching_pairs.py` refuses to run on a deployed service.

## 7. Shipping

Follow `[ship-loop]` and `[problems-log]`. What the rules do not say:

- `./dev.sh check` takes about an hour in the cloud container. Run it once,
  in the background, on the final tree. Before that, run only the tests
  that mention what you touched:
  `grep -lE '<names you changed>' tests/*.py`, then pytest on those files.
- A test that assumed the old state is evidence, not an obstacle: read why
  it assumed it. Fix the assumption, never the assertion's point.
- Add the PROBLEMS.md section, then `python tools/problems_index.py`. A new
  or changed rule needs its marker in `docs/claude/` and its line in
  `CLAUDE.md` (`tests/test_claude_md.py`).
- Commit when the tree is coherent and say plainly whether the full check
  has finished. Push to the session's branch (`[branch-no-pr]`).

## 8. The last message

Lead with what changed for the listener or the owner, not with what you
did. Put numbers in one small table. Say what is unverified and what it
would cost to verify. End with the one thing the owner has to decide, if
there is one. Keep working detail in PROBLEMS.md, not in the reply.

## 9. Three answers, weak and better

**"Add a three-minute example."**
Weak: write one and save it in `examples/`, where the writer reads it at
once.
Better: read `examples/README.md`; see that the folder teaches length and
that three minutes is missing; see in `docs/story_shapes.md` that no
example is a comparison; write one that fills both; land it unloaded; score
it with `episode_quality.measure` beside the house examples; let the owner
read it.

**"Do the pairs work?"**
Weak: "All six pass and improve the writing."
Better: "All six pass the tool's two gates (table). Whether they change
what the writer produces is unmeasured; `tools/editor_eval.py` with and
without them would say, for a few dollars."

**"The writer keeps saying 'I don't have a confirmed result'. Fix it."**
Weak: add a line to the writer's instructions forbidding it.
Better: the writer's instructions already forbid it, twice
(`script_generator.py`, from §288), and `[missed-not-absent]` rules out
saying the gap aloud instead. Four of 36 drafts did it anyway, in seven
sentences, three of the four at low effort and three about a moving price.
So count it, read those packets for what they share, and find the guard:
`MaterialGuard` was narrowed to "I", "me" and "my" in §288, and the drafts
say "we". The fix is to widen that guard with §288's false positives kept
as tests, and to tell the owner what a dropped sentence costs when it is
the one carrying the figure.
