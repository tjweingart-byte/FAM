# Before-and-after pairs (§522)

Each `.txt` here is the same episode written twice from the same sources: a
draft with the flaws FAM's code measures name, and the house voice. The
writer's instructions show them (`TEACHING_PAIRS`, at most six).

```
<the question>

BEFORE:
<the draft, as spoken>

AFTER:
<the rewrite, as spoken>

WHY: <one line on what changed>
```

They are made **before launch only**, on a developer's machine, by
`python tools/teaching_pairs.py --propose` from `tools/editor_eval.py`'s
frozen scripts, and land in `~/.fam/teaching_pairs/`. Read each and move the
ones worth teaching here by hand. No episode a listener hears is ever
rewritten. `python tools/teaching_pairs.py --check` checks the files.

**The six here (§619, adopted §620)** were made a second way: each BEFORE
is a script the writer (Haiku 5.5) wrote from a packet the tool froze on
2026-11-16; each AFTER was rewritten by hand from the same packet in the
session, in place of the Opus call, then passed the tool's two gates (the
code's measures and the Opus grader; the numbers are in §619). Their facts
were true of their packets that day and are not kept current - the writer
is told to learn what changed, never to borrow a pair's facts. The writer
is shown at most six, in file-name order, so a seventh file here is never
read: replace one instead.
