# Before-and-after pairs (§502)

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
