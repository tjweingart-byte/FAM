# FAM agent rule books

One file per agent, plus `every-agent.md`, which every agent also follows.
Each is written in plain English: the rules an agent **must follow and cannot
break**.

**These files are drafts** (version 1, 2026-10-28). The owner keeps the
governing copies **outside Claude**, where no session and no agent can change
them. When a held copy and anything in this repository disagree, the held
copy wins and the repository is corrected. An agent reads its rule book; it
never edits one.

**Once agents run, these files are locked.** Each runs only while its hash
matches `MANIFEST.json`, signed with a key only the owner holds; a changed
or unsigned rule book holds its agent, and a test fails before it can ship.
To change one, the owner edits it and signs a new manifest - nothing else
can. How it works: `docs/FAM_AGENTS.md`, Shared rules, Locked rule books.

| Agent | Name | Rule book |
|---|---|---|
| Every agent | - | [every-agent.md](every-agent.md) |
| Overlord | Netenyahu | [netenyahu.md](netenyahu.md) |
| Contextualizer | C3P0 | [c3p0.md](c3p0.md) |
| Editor in chief | Shakespeare | [shakespeare.md](shakespeare.md) |
| Script supervisor | Mclovin | [mclovin.md](mclovin.md) |
| Housekeeper | Consuela | [consuela.md](consuela.md) |
| Retriever | Sonic | [sonic.md](sonic.md) |
| IT guy | Keith Wong | [keith.md](keith.md) |
| Categorizer | Nando | [nando.md](nando.md) |

The reasoning behind each rule is in `docs/FAM_AGENTS.md`; these files hold
only the rules.
