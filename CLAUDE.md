# CLAUDE.md — standing rules for the codewhy repository

`codewhy` is a Python CLI: point it at a local folder of Python/JS/TS code and it
writes Markdown (one paste-ready `PROJECT_DOCS.md` by default) explaining what
each file does, why it exists, and how the pieces connect. Deterministic parsing
produces the facts; an LLM writes the explanations. The goal is a tool real
people run with one command (`uvx codewhy scan .`), not a demo.

## Documents, in order of authority

1. `docs/ARCHITECTURE.md` — source of truth for behavior and contracts.
2. `docs/TESTS.md` — the test plan the architecture is held to.
3. `tasks/NN-*.md` — the work order you are executing right now. It defines
   scope. `tasks/ALL-TASKS.md` is the index.
4. `docs/PROJECT_SPEC.md` — background rationale only. If it disagrees with the
   architecture, the architecture wins.

## How every task runs

Before changing anything:

1. Read this file, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, and the whole task
   file. Do not skim the task's *Out of Scope* and *Contracts* sections.
2. Run `git status`; read the existing code the task touches; read
   `logs/<task>.md` if it exists.
3. Write a short plan into the work log (what you will build, in what order)
   before the first code change.

While working:

- **Stay inside the task's scope and allowed files.** No "while we're here"
  improvements. If you find a defect or need something outside scope, log it and
  tell the project lead; do not silently expand the task.
- **Do not invent architecture.** If the task or the architecture is silent or
  contradictory on a point that matters, stop and ask. Small, local decisions
  (private helper names, internal data layout) are yours; record any that later
  tasks will depend on in the work log.
- **If a contract in `docs/ARCHITECTURE.md` must change** (a config key, CLI
  flag, schema, rule), change the document in the same change set and say so in
  the work log. Never let code and architecture drift.
- **Test as you go.** Write the tests the task lists, run them often, and fix
  the code, not the test, unless the test is wrong (say so in the log).
- **Never claim something works because a document says it should.** Run it.
- **Dependencies:** add only the runtime/dev packages the task names. Anything
  else needs approval.

## Engineering conventions

- Python `>=3.11`, `src/` layout, type hints on all public functions, small pure
  functions in core logic (parsing, resolution, budgeting, keys). I/O lives at
  the edges (`cli.py`, `ignore.py` discovery, providers, cache DB, renderers'
  file writes).
- Paths in every data structure are **project-relative POSIX strings**
  (`src/pkg/mod.py`), never `Path` objects and never backslashes. Convert at the
  filesystem boundary only. Text read from disk has `\r\n` normalized to `\n` so
  hashes and line counts are identical on every OS.
- **Determinism is a feature.** Sort everything you iterate (files, modules,
  edges, dict keys in serialized output). The same input must produce
  byte-identical output; the cache and the idempotence tests depend on it.
- No `print` in library code. Library modules use `logging` (module-level
  logger); only `cli.py` writes to the terminal (via `rich`/`typer`).
- Never log or store source code, prompts, model output, or API keys in log
  files, the cache beyond its defined `result_json`, or error messages.
- No global mutable state. Pass dependencies (config, provider, clock) in.
- Everything must work on macOS, Linux, and Windows (use `pathlib`, no shell
  assumptions, no POSIX-only calls).
- Network and LLM access happen **only** in provider code. Every test outside
  the E2E layer runs offline with `FakeProvider` or mocked HTTP.

## Commands

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"      # Windows: .venv\Scripts\python
.venv/bin/python -m pytest -q                    # default: no live LLM
.venv/bin/python -m pytest -q -m live_llm        # only when the task says so
```

## Work log

Each task has `logs/NN-<slug>.md`. Keep it updated as you go (create the `logs/`
folder if missing). Contents: date, plan, decisions made, commands run with
results, deviations from the task and why, open issues, and for any check that
needs hardware, a key, or a model: **Passed / Failed / Not run (reason)**. No
secrets, no private code.

## Handoff report (end of every task)

Report: what changed (files), what was verified (commands + results), what is
untested or unresolved, any architecture edits, any decisions later tasks must
know. Then run `git status` and `git diff`, confirm no unrelated changes.

## Git

Do **not** commit, push, open a PR, or merge unless the user explicitly
authorizes that specific action. If authorized to commit: review the staged
diff, include only intended files, no AI attribution lines in commit messages.

## Definition of done

```
Implementation complete
  + automated tests passing (or reported as not run, with reason)
  + live/hardware checks recorded in the work log (Passed/Failed/Not run)
  + scope verified against the task file
  + git diff reviewed
  + work log updated
  + no contracts in docs/ARCHITECTURE.md violated or silently changed
  = ready for review
```
