# codewhy — Task Index

Nine work orders, executed **strictly in order**. Each ends demoable with its tests green. `docs/ARCHITECTURE.md` is the source of truth; `docs/TESTS.md` is the test plan; `CLAUDE.md` holds the standing rules.

| # | Task ID | File | Delivers | Needs (user) |
|---|---|---|---|---|
| 01 | CW-01 | `01-scaffold-config-discovery.md` | Installable package, config, discovery/ignore, module model, `init`, `config show` | nothing |
| 02 | CW-02 | `02-extraction.md` | Python + JS/TS extraction, units, tree-sitter wheel matrix | GitHub remote (for the OS matrix) |
| 03 | CW-03 | `03-resolution-graph-entrypoint.md` | Import resolution, graph, entry point, `inspect` | two real projects to eyeball |
| 04 | CW-04 | `04-providers-budget-schemas.md` | Ollama + openai-compat + `gemini` preset, budget, schemas, consent, `doctor` | Ollama and/or Gemini key; a Gemini model id |
| 05 | CW-05 | `05-summarization-passes.md` | Prompts, file/module/project passes, chunking, ladders, retries, orchestrator | live model for the quality check |
| 06 | CW-06 | `06-render-and-scan.md` | Rendering (levels, docs-tree), real `scan`, golden files | live model; **the paste test** |
| 07 | CW-07 | `07-cache-incremental.md` | Cache, resume, byte-identical re-runs | live model |
| 08 | CW-08 | `08-cli-robustness.md` | `--dry-run`, progress, exit codes, failure matrix, atomic writes | live model |
| 09 | CW-09 | `09-ship.md` | Packaging, CI, README, evaluation, release | GitHub, PyPI, corpus + ground-truth answers |

## Repo setup (once, before task 01)

```text
codewhy/
├── CLAUDE.md                  <- from this package
├── docs/
│   ├── ARCHITECTURE.md        <- from this package
│   ├── TESTS.md               <- from this package
│   └── PROJECT_SPEC.md        <- from this package
└── tasks/
    ├── ALL-TASKS.md           <- this file
    └── 01-… through 09-…
```

Run `git init`. Reserve the PyPI name `codewhy` early (a placeholder release) so it cannot be taken while you build.

## How to run a task with Claude Code

Start a **fresh Claude Code session per task** (clean context), from the repo root, and give it exactly this, changing only the number:

```text
Execute tasks/03-resolution-graph-entrypoint.md.

Follow CLAUDE.md. Read docs/ARCHITECTURE.md, docs/TESTS.md, the task file, and the work
logs of earlier tasks before changing anything. Write your plan into the task's work log
first. Stay strictly inside the task's scope. Do not commit unless I say so.
When done, give me the handoff report from CLAUDE.md.
```

Between tasks, you (not the agent) should:

1. Read the work log and the `git diff`.
2. Run `.venv/bin/python -m pytest -q` yourself.
3. Do the task's manual verification and any "needs (user)" items above.
4. Only then commit and start the next task.

## Rules that keep the build honest

- A task is done only when its tests pass **and** its live/hardware checks are recorded as Passed / Failed / Not run (reason). "Not run" is acceptable; a missing entry is not.
- If a task forces a change to `docs/ARCHITECTURE.md`, the change lands in the same commit and is named in the work log. Never let code and architecture drift.
- If something in a task contradicts the architecture, the agent must stop and ask; it must not pick a side silently.
- Do not start task N+1 with failing tests from task N.

## Where the real risks sit (look closely at these)

- **Task 02:** the tree-sitter wheel matrix. A failure here changes what "one command" can promise.
- **Task 04:** whether reasoning tokens eat `max_output_tokens` on Gemini; the live check settles the preset defaults.
- **Task 05:** prompt quality. No unit test can tell you the model explained *why* a file exists. Read real output.
- **Task 06:** the golden files freeze the layout; review it by eye before committing it. Do the paste test.
- **Task 09:** the evaluation may give an unflattering answer. Report it anyway.
