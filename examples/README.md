# Style examples

Drop briefings in here that you'd want FAM to sound like. They get shown to the
model as examples of the house voice before it writes.

**This is the most direct control you have over the writing.** Describing a
style in rules works loosely; showing three scripts works well. The model learns
the voice, the rhythm and the shape — not the facts, so the topics can be
anything.

## Format

One file per example. Name it `<minutes>-<slug>.txt`, so the model can see how a
briefing scales with length:

```
examples/1-offside-rule.txt
examples/3-interest-rates.txt
examples/5-suez-canal.txt
```

First line is the query someone would have typed, then a blank line, then the
script exactly as it should be spoken:

```
what is the offside rule

A player is offside if they're nearer the opponent's goal than both the ball
and the second-last defender at the moment a teammate plays it forward...
```

## Filing it on the tree (§307)

Two lines above the query say what the example is about and how it is
built, so the writer is shown the examples nearest its own episode rather
than all of them:

```
# node: formula one
# shape: sports_recap
how did max verstappen do in the singapore sprint

Max Verstappen finished...
```

- `node` is a category-tree id (`python tools/categories_report.py --tree`
  draws them; a facet such as `sports` is fine for a general one). The most
  specific node the example is genuinely about.
- `shape` is one of the brief's story shapes: `breaking`, `business`,
  `sports_recap`, `sports_preview`, `in_progress`, `explainer`,
  `historical`, `trend`. Shape outranks topic when examples are chosen - a
  recap teaches a recap whatever the sport - so the shapes with no example
  yet (all but explainer, historical and sports recap) are the most useful
  ones to write next.

A third line, `# phase:`, is for an example that only fits one time of a
league's year (§308): `preseason`, `regular_season`, `postseason` or
`offseason`. The league comes from the node (the Eagles take the NFL's
calendar, `seasons.CALENDARS`), and an example tagged for one phase is never
shown in another - the Singapore sprint recap is `regular_season`, so an
offseason Formula One question is not shown it. Leave it off for anything
that fits all year; an offseason example (a trade, a contract, the draft) is
the most useful one to write next.

A file without the two lines still loads; it is simply never singled out.
`/admin` → Episode quality shows which examples shaped which episodes and how those
scored, and which busy nodes have nothing of their own.

## What makes one useful

- **Write it to be heard, not read.** Say it out loud. If you stumble, rewrite.
- **The first sentence matters most.** It's the thing the model copies hardest,
  and the thing a listener judges you on. Make it a fact, not a preamble.
- **Vary the shape.** An explainer, a recap, a "why does this happen" — briefings
  aren't all the same form, and one example teaches one form.
- **Include a short one and a long one.** How a briefing grows from one minute to
  five is exactly where padding creeps in; showing it is better than describing it.
- **Answer the question the example asks.** This is the one that matters most.
  Whoever typed that query wanted to know something; by the last line they
  should know it. An example that is beautifully told and leaves you unsatisfied
  teaches exactly the wrong lesson.
- **End the way you want it to end.** Endings get copied hardest, and this is
  the next most valuable thing an example can teach. **Land it and stop.** The
  last line is the most concrete thing in the piece, and then it ends,
  mid-stride. No summary, no "and that's the story of", no question asked of
  the listener — and **no hook**: no dangling thread, no "but that raises
  another question", no pointing at what you are not covering.

  > **This section used to say the opposite** — "don't conclude, widen; leave
  > one thing unresolved and stop pointed at it" — and that rule was reversed
  > in PROBLEMS.md §48. Heard back to back, a thread left open at the end of
  > every episode is a hook at the end of every episode, which is a tease, and
  > it was asked to be removed twice. It survived here after it was removed
  > from the prompt, which is precisely the failure CLAUDE.md warns about: a
  > setting is settled only where it is copied, and an example file has turned
  > deleted behaviour back on in this project before. Anything genuinely
  > unresolved is said *inside* the piece, plainly, and then the piece carries
  > on.
- **Situate them in the first two sentences.** Who, what, when, in
  particulars — enough that they know what they are listening to before the
  third sentence. That is not the same as orienting them: never explain why
  the topic is worth their time, never say "as you may know", never open on
  background. History earns its place by explaining the present, not by
  preceding it.
- **Never state a current fact the piece has not established.** An example
  that confidently asserts a score, a price or a result teaches the model that
  confidence is the house voice, and that is the one lesson this project
  cannot afford. Prefer topics whose facts do not move.
- You do **not** need to add the `<<NEXT: ...>>` line to an example. That is
  machine metadata the app strips; examples are about how the writing sounds.
- **Don't over-polish.** Write what you'd genuinely want in your ears on a walk.

Two or three good ones shift the output a lot. Past about five, returns drop
off for *voice* - but since the writer moved to Haiku (PROBLEMS.md §286-287)
the folder also teaches *length* and *range*: one example at each length (1,
2, 4 and 5 minutes) and a two-minute one per facet (sport, money, health,
tech, culture, world, science). Since §307 a writer is shown only the
nearest few (`EXAMPLES_PER_EPISODE`), chosen by shape, branch and length,
so an example no longer costs every call - but a weak one is still shown to
every episode near it; prefer replacing it to adding another. `2-singapore-sprint` is the one
built on a dated result: it was written from its sources, and is here for
the recap's shape, not its facts.

Anything in here is loaded automatically at startup — no code change needed. An
empty folder simply means no examples, and the prompt is unchanged.
