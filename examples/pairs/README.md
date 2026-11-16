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

**None is committed here.** Six were (§621) and were taken back out after
they were measured (§624, §625): they are in `../candidates/pairs/`, with
what the measurement found. The writer is shown at most six, in file-name
order, so a seventh file here is never read.
