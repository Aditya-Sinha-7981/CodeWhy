# codewhy — Shregna's plan

You own the parsing track (tasks 02 and 03), the document renderer (task 06's render half), the failure-boundary matrix test (task 08), and the quality evaluation (task 09). Read `CLAUDE.md`, `docs/ARCHITECTURE.md` (especially *Layer 1*, *Layer 2*, *Output*), `docs/TESTS.md`, and `tasks/ALL-TASKS.md` first.

## Your tasks, in order

| # | Starts when | Branch | You deliver | Reviewer | Waiting on you |
|---|---|---|---|---|---|
| 02 | Aditya merges 01 | `task/02-extraction` | Python + JS/TS extraction, units, fixtures, wheel-check script and CI workflow | Aditya | You, for 03; Aditya's 05 |
| 03 | 02 is merged | `task/03-resolution-graph` | Resolvers, graph, entry point, `inspect`, `nested_modules` fixture | Aditya | Aditya's 05 |
| 06 (render) | 05's contracts commit is pushed (`PassResult`, `RunResult`), then in parallel with the rest of 05 | `task/06-render` | Section builders, single-file with levels, docs-tree, writer, golden files (Requirements 1–4 and 6 of `tasks/06-render-and-scan.md`) | Aditya | Sheikh's `scan` wiring |
| 08 (matrix) | 07 and the rest of 08 are merged | `task/08-failure-matrix` | `tests/test_failure_matrix.py` (Requirement 4 of `tasks/08-cli-robustness.md`) | Aditya | — |
| 09 (eval) | 06 merged and a provider is configured | `task/09-eval` | `eval/run_eval.py`, `eval/check_mentions.py`, `eval/RESULTS.md` | Aditya | — |

## Before you can start (do these now)

1. Set up the dev environment: Python 3.11+, venv, `pip install -e ".[dev]"` once 01 lands.
2. **Author the task 02 fixtures early.** They are just files: `flat_scripts/` (a small backtracking solver split across `main.py`, `solver.py`, `utils.py`, using absolute local imports) and `python_js_mixed/` (see the exact list of import forms in `tasks/02-extraction.md`, Requirement 6). Keep them realistic, not lorem ipsum.
3. Skim the tree-sitter Python docs so you can verify the API shape the agent uses against the installed version.
4. You need a GitHub remote (Sheikh sets it up) before the OS/Python wheel check in task 02 can run. If it isn't there when you finish, record **Not run (no remote)** in the log; do not claim a pass.

## Notes per task

**02 / 03 — the hard parts**
- Task 03's acceptance test is the flat-script case (`import utils` in `main.py` must produce an edge). Write that test first. A resolver that only handles relative imports yields zero edges on the projects this tool is built for.
- Check `inspect` output by hand on two real projects, comparing at least 10 files' edges against the actual imports. Log any import that landed in `external_imports` or `unresolved_imports` but should have resolved.
- If any cell of the wheel matrix fails (OS × Python), stop and tell Aditya. It changes what "one command, no compiler" can promise.

**06 (render) — the layout is frozen by golden files**
- The exact heading layout is in `tasks/06-render-and-scan.md`, Requirement 1. Review the Level 0 golden output **by eye** before committing it.
- Model text must never create headings (escape line-leading `#`), and failed results must render as a visible `[generation failed]` placeholder.
- Until Sheikh's wiring is merged, test with `FakeProvider` and a `fixed_clock`.

**08 (matrix) — be the outsider**
- Write the matrix from the architecture's *Failure boundaries* table and `tasks/08-cli-robustness.md`, **not** by reading Sheikh's implementation first. Fresh eyes are the point.
- If the real behavior differs from the table, raise it with Aditya. Fix the code or the architecture; never bend an assertion to make it pass.

**09 (eval)**
- Aditya, Sheikh, and you each write questions **with ground-truth answers for your own projects**. The agent must not invent ground truth. Mark unanswered items `TODO(user)`.
- You run the three-condition evaluation (listing only / raw source / generated docs) and the mention check; scoring is blind where practical. Report unflattering results too.

## Keys and environment

- Use your own free Gemini key (Google AI Studio) or a local Ollama. A 14B model needs a capable laptop; otherwise use the free key or a smaller model via `llm.model`.
- Only live checks need a key; the test suite is offline. Never commit a key, paste it into a work log, screenshot, or prompt, and treat anything the agent can see in its session environment as exposed.
- **Never ask for or use Aditya's company key.** It is used only by him, in his own terminal, for the demo.

## Working rules

1. One branch per task, one PR, a reviewer from the table above, `main` always green.
2. Fresh Claude Code session per task, using the prompt from `tasks/ALL-TASKS.md`:

```text
Execute tasks/02-extraction.md.

Follow CLAUDE.md. Read docs/ARCHITECTURE.md, docs/TESTS.md, the task file, and the work
logs of earlier tasks before changing anything. Write your plan into the task's work log
first. Stay strictly inside the task's scope. Do not commit unless I say so.
When done, give me the handoff report from CLAUDE.md.
```

3. Before every PR: run `.venv/bin/python -m pytest -q` yourself, read your `git diff`, and make sure the work log's live-check lines say Passed / Failed / Not run (reason).
4. Any change to `docs/ARCHITECTURE.md` goes through Aditya, in the same PR as the code that forces it.
