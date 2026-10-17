# codewhy Task Specification

> **This file is a work order for the assigned developer and their AI coding agent.**
>
> This task defines the scope of the assigned work. Do not expand the scope without explicit approval from the project lead.

---

## Task Information

**Task:** `CLI completeness and robustness: --dry-run, progress, exit codes, failure-boundary matrix, atomic writes, interrupts`

**Task ID:** `CW-08` (index: `tasks/ALL-TASKS.md`)

**Depends on:** CW-01 through CW-07

**Branch:** `task/08-cli-robustness` (suggested; create only when the user wants a branch)

**Priority:** `High`

**Status:** `Not Started`

**Work log:** `logs/08-cli-robustness.md`

---

# 1. Goal

## Objective

Make the tool behave well when things go wrong and informative when they go right. Deliverables:

1. **`scan --dry-run`:** a plan with exact file-pass numbers and upper-bound module/project numbers, no provider calls, no writes.
2. **Progress output** with `rich` (per-item lines, TTY vs non-TTY, `--quiet`) and a trustworthy final summary.
3. **Exit-code audit** (0/1/2, plus interrupt handling).
4. **The failure-boundary matrix:** one integration test for every row of the architecture's table.
5. **Atomic output writes** so a crash never leaves a half-written document.
6. **Graceful interrupt (Ctrl-C):** progress already cached is kept, a clear message is printed.
7. **Input and environment edge cases** (paths with spaces/unicode, unwritable folders, empty projects, `scan` on a file instead of a folder).

## Why This Exists

First impressions decide whether a stranger keeps using a CLI. A run that hangs silently for twenty minutes, dies with a traceback, or leaves a corrupted output file loses the user. And hosted-API users need to see the cost of a run before they pay it, which is what `--dry-run` is for.

---

# 2. Authoritative Documentation

* `CLAUDE.md`
* `docs/ARCHITECTURE.md` — *CLI surface* (flags, exit codes, progress, `--dry-run`), *Failure boundaries* (the matrix source), *Privacy and remote endpoints*, *Layer 3* (resume semantics)
* `docs/TESTS.md` — *CLI*, *E2E*

---

# 3. Scope

## In Scope

* `cli.py` (dry-run, progress, interrupts, error handling), `src/codewhy/progress.py`, `src/codewhy/plan.py` (dry-run planning), `src/codewhy/render/writer.py` (atomic writes, if the writer from task 06 lives elsewhere, extend it there).
* Tests: `tests/test_dry_run.py`, `tests/test_progress.py`, `tests/test_exit_codes.py`, `tests/test_failure_matrix.py`, `tests/test_atomic_write.py`, `tests/test_cli_edge_cases.py`.

## Out of Scope

* New features, flags, providers, or output formats.
* Changing any contract in the architecture (if the matrix test reveals the architecture is wrong, fix the architecture **and** tell the project lead).
* Parallelism, ETA estimation, logging to files, telemetry.

Do not implement "while we're here" improvements outside this scope.

---

# 4. Allowed Files / Areas

```text
src/codewhy/cli.py
src/codewhy/progress.py
src/codewhy/plan.py
src/codewhy/render/writer.py                   (or wherever the output writer lives)
src/codewhy/pipeline.py                        (callback plumbing only)
tests/test_dry_run.py
tests/test_progress.py
tests/test_exit_codes.py
tests/test_failure_matrix.py
tests/test_atomic_write.py
tests/test_cli_edge_cases.py
logs/08-cli-robustness.md
```

### May Modify If Required

```text
docs/ARCHITECTURE.md   (failure-table or CLI corrections — logged)
docs/TESTS.md
```

---

# 5. Dependencies

| Dependency | Type | Notes |
|---|---|---|
| CW-06 `scan`, CW-07 cache (`peek`, key functions, resume) | Hard | |
| `rich` | Runtime | Already a dependency. |

### Dependency Status

* [x] Dependency exists and is ready
* [ ] Dependency is being developed separately
* [ ] Dependency requires coordination
* [x] No external dependency

---

# 6. Contracts That Must Be Preserved

### Architecture

* **`--dry-run`:** no LLM calls, no files written (including `.codewhy/`), no remote-acknowledgement prompt, no provider preflight. Reports: files discovered; files skipped with reasons; cache hits vs misses; number of LLM calls and estimated input tokens. **File-pass numbers are exact** (prompts are assembled without calling the model; chunked files report their chunk + combine call counts). **Module/project-pass numbers are upper bounds** because their prompts depend on file summaries that may not exist; say so in the output. Read-only cache lookups only (`peek`).
* **Exit codes:** `0` all files documented (ignored/unsupported files do not count); `1` fatal (bad config, provider unreachable, model missing, remote not acknowledged, refused overwrite, auth/bad-request abort, no supported files, unwritable output, cache locked); `2` completed but ≥1 file skipped for parse/size/decode reasons or ≥1 pass ended `[generation failed]`. Interrupted run: `130` with the message "Interrupted. Progress is saved in the cache; re-run to resume."
* **Progress:** one line per pass item, e.g. `[7/18] file  src/solver.py  (generated)` / `(cached)` / `(failed)`; final summary: `18 files, 11 cached, 7 generated, 0 failed, 1 skipped. PROJECT_DOCS.md ≈ 9,400 tokens (level 0).`; `--quiet` suppresses item lines but keeps the summary and errors. Skip details print before the summary as `skipped <path> (<reason>)`.
* Fail-fast: provider preflight and remote acknowledgement occur before any project file is read.
* Output is written only after all overwrite checks pass; a refusal leaves the tree untouched.

### Failure boundaries (each row gets a test; see Requirement 4)

Provider unreachable; Ollama model not pulled; remote not acknowledged; tree-sitter grammar missing for a found file; malformed Python/JS/TS; file over `max_file_bytes` or undecodable; SQLite cache corrupt/unknown schema; LLM output malformed (retry once, then placeholder, not cached); transient provider error (backoff, then placeholder); output target exists and is not codewhy's; single function larger than chunk budget (truncation is **not** a failure).

### Safety

* Output writes are atomic per file: write to a temp file in the same directory, then `os.replace`. A crash or Ctrl-C never leaves a truncated document, and stale temp files are cleaned up on the next run.
* Never print API keys, prompts, source text, or model output (other than a one-line reason for a failure) to the terminal.

---

# 7. Implementation Requirements

### Requirement 1 — `scan --dry-run` (`plan.py`)

`plan_scan(project, config, cache) -> Plan` producing:

* discovered file count; skipped files with reasons; modules count; whether an entry point was found;
* per pass: items, hits (via `CacheDB.peek` with the exact keys), misses, **calls** (file passes: exact count including chunk + combine calls for oversized files; module/project: one call per miss, labelled upper bound), **estimated input tokens** (sum of `estimate_tokens` of the assembled prompts; for module/project use the prompt assembled from placeholder Purpose/Responsibility texts of maximum plausible length bounded by their schema max lengths, labelled upper bound);
* total estimated calls and tokens, and a one-line hint when a remote provider is configured ("This would send ≈ N tokens of your source to <host>").

`scan --dry-run` builds config and project but **does not build or contact the provider**, does not prompt for acknowledgement, does not create `.codewhy/`, and writes nothing. If the cache DB does not exist, all items are misses. Output goes through `rich` tables when on a TTY and plain aligned text otherwise.

### Requirement 2 — Progress (`progress.py`)

* A `ProgressReporter` implementing the `on_item` callback: TTY → `rich` live line/spinner plus per-item lines; non-TTY or `--quiet` → plain lines or nothing; never emits control codes when not a TTY.
* Windows-safe: if the console encoding cannot represent `—`, `→`, `…`, fall back to ASCII equivalents for **terminal** output only (files stay UTF-8).
* The summary is built from `RunResult.counts` plus skips; numbers must match what actually happened (test with scripted scenarios).

### Requirement 3 — Exit codes and interrupts

* Centralize exit-code decisions in one function `exit_code_for(run_result, skips, fatal) -> int` with a table-driven test.
* `KeyboardInterrupt` during the pipeline: stop cleanly, close the cache DB, remove temp output files, print the interruption message, exit 130. No cleanup of cache rows (resume must work). Output files from the previous run, if any, are left untouched (nothing half-written replaced them).

### Requirement 4 — Failure-boundary matrix test

`tests/test_failure_matrix.py`: a parametrized table, one case per boundary row listed in §6, each asserting **(a) the exit code, (b) the user-visible message content, (c) what was and was not written, (d) that the run continued or stopped as the table says, (e) provider call counts**. Use `FakeProvider` scripted failures, `broken_files`/`oversized_file` builders, and monkeypatching for the grammar-missing and cache-corruption cases. If a row cannot be reproduced as specified, record why in the log and fix the architecture or the code (never the test to hide it).

### Requirement 5 — Atomic writes (`render/writer.py`)

`write_atomic(path: Path, text: str)` writes UTF-8 with `\n` newlines to `<path>.<random>.tmp` in the same directory, flushes and fsyncs, then `os.replace`. Docs-tree mode computes the full set of writes and all overwrite checks first, then writes each file atomically. A leftover `*.tmp` from a crashed run is removed at the start of the next `scan` (only files matching the writer's temp naming inside the output locations).

### Requirement 6 — Edge cases

* `PATH` does not exist → exit 1 with the path in the message. `PATH` is a file → exit 1 ("expected a folder"). `PATH` with spaces or non-ASCII characters works end to end.
* Output location unwritable (read-only folder) → exit 1 naming the path; nothing partial.
* Empty project / no supported files → exit 1 with the supported-extensions message (exists since task 06; verify under `--dry-run` too: it reports the same condition).
* `codewhy.toml` present but invalid → exit 1 listing all problems.
* A source file deleted between discovery and extraction → counted as an `unreadable` skip, not a crash.
* Very long paths and deep trees do not crash discovery.

### Error Handling

* Expected failures print one clear message (what, why, how to fix) to stderr and exit non-zero; **no tracebacks** for expected failures. Unexpected exceptions print a short "internal error" message with the exception type and a hint to re-run with `CODEWHY_DEBUG=1` for the traceback, then exit 1 (implement the env var).
* Warnings (rebuilt cache, skipped files) are collected and printed after the item lines and before the summary.

---

# 8. Testing Requirements

## Automated Tests

* `test_dry_run.py`: zero provider calls (`FakeProvider.calls` empty, and `build_provider` never invoked); zero files written (snapshot before/after, including no `.codewhy/`); no acknowledgement prompt even for a remote config; file-pass counts exact (equal to the calls a real run makes, for a project with an oversized file included); module/project labelled upper bounds and ≥ actual calls of a real run; cache hits/misses correct against a pre-populated cache; skipped files listed with reasons; remote hint line; plain-text output when not a TTY.
* `test_progress.py`: item lines for generated/cached/failed; `--quiet`; no control codes when not a TTY; ASCII fallback when the console encoding is `ascii` (simulate); summary counts match scripted scenarios; skipped lines precede the summary.
* `test_exit_codes.py`: table-driven `exit_code_for`; real scans producing 0, 1, 2; interrupt → 130 (inject `KeyboardInterrupt` from `FakeProvider` on a chosen call) with cache rows kept and **no** half-written output; re-run after the interrupt resumes (only remaining calls).
* `test_failure_matrix.py`: every boundary row (see Requirement 4).
* `test_atomic_write.py`: the target never exists in a partially written state (simulate a failure between temp write and replace: previous content intact); temp files are in the same directory; stale temp files removed on the next run; docs-tree writes happen only after all overwrite checks pass (a refused file leaves **no** new files).
* `test_cli_edge_cases.py`: nonexistent path; path is a file; spaces/unicode path; read-only folder (skip with a recorded reason on platforms where unsupported); invalid `codewhy.toml`; file deleted between discovery and extraction; `CODEWHY_DEBUG=1` shows a traceback for an injected unexpected exception, default shows the short message.

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected result:

```text
all tests pass offline; every failure-boundary row has a passing test
```

## Hardware / Model-Dependent Checks

Record each as Passed / Failed / Not run (reason) in `logs/08-cli-robustness.md`:

* `scan --dry-run` on a real project with a real hosted configuration; compare its predicted call count with the actual calls of the real run (they should match for file passes).
* Ctrl-C a real run midway; confirm the message, exit code 130, and resume.
* A full real run in a terminal on macOS/Linux and in Windows Terminal/PowerShell if available; record progress rendering quality and any encoding problems.

## Manual Verification

1. Run `scan` in a non-TTY context (`| cat`, CI): confirm plain, line-oriented, readable output.
2. Run with a nonexistent model, a wrong host, and a bad `GEMINI_API_KEY` (use a dummy): confirm the messages tell you exactly what to do.
3. Kill the process with `kill -9` mid-write if you can time it; confirm no truncated output file replaced the old one and the next run cleans up.

---

# 9. Acceptance Criteria

* [ ] `--dry-run` is read-only, provider-free, and its file-pass numbers match real runs.
* [ ] Progress and summary are accurate and degrade gracefully (non-TTY, quiet, limited encodings).
* [ ] Exit codes follow the contract; interrupt exits 130 and resume works.
* [ ] Every failure-boundary row has a passing integration test with exit code, message, writes, and call counts asserted.
* [ ] Output writes are atomic and overwrite checks precede all writes.
* [ ] Expected failures never show tracebacks; `CODEWHY_DEBUG=1` shows them.
* [ ] Live checks recorded (or **Not run (reason)**).
* [ ] No new dependencies; no secrets/source/prompts printed.
* [ ] `git diff` reviewed; work log updated.
* [ ] If a commit was authorized: staged diff reviewed, only intended files, no AI attribution, no push/PR.

---

# 10. Known Risks

* **Dry-run upper bounds can be wildly pessimistic** for module/project passes. Say "upper bound" in the output; do not present them as predictions.
* **Windows consoles:** encoding and ANSI support vary; this task's ASCII fallback and non-TTY handling are the mitigation, and CI on Windows (task 09) is the proof.
* **Exit code 2 vs 0 semantics** will be scripted against by users. Once published, changing them is a breaking change; get them right now and document them in the README.
* **`os.replace` atomicity** is per file, not per tree; docs-tree mode can still end up partially updated if a crash happens between files. The next run repairs it; state this honestly rather than claiming transactional output.
* **Interrupt timing:** a `KeyboardInterrupt` arriving inside SQLite or inside the temp-file write must not corrupt either; keep critical sections short and test the two windows.
* **Matrix tests tempt test-fitting.** If a row's real behavior differs from the table, change the code or the architecture deliberately; do not bend the assertion.

---

# 11. Open Questions

* **requires clarification:** Should `--dry-run` also accept `--output` and report the render-level prediction? — Recommended: no (prose size is unknowable before the run); report only calls and tokens in.
* **requires clarification:** Add a `--verbose` flag for debug logging? — Recommended: not now; `CODEWHY_DEBUG=1` is enough for v1.

---

# 12. Handoff Notes

* Task 09 documents exit codes, `--dry-run`, and progress behavior in the README and runs the Windows/macOS/Linux CI matrix that validates the terminal and path behavior built here.
* Record in the log: any architecture edits from the matrix, observed dry-run vs real-run call-count agreement, Windows encoding findings, and the final exit-code table.

If the task is complete:

> No outstanding handoff notes.

---

# 13. Definition of Done

See `CLAUDE.md` → *Definition of done*. Do not commit, push, open a PR, or merge unless the user explicitly authorizes that specific action.

## Final Agent Instruction

Before making changes: read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, this file, and the logs of tasks 01–07; run `git status`; write your plan into `logs/08-cli-robustness.md`.

During implementation: never bend a test to hide a real discrepancy; stay in scope; do not invent architecture; keep dry-run strictly read-only.

If the task requires something not covered here, stop and ask the project lead.

Before handing off: run the tests, run the live checks you can (record the rest as Not run), review `git diff` and `git status`, update the log, and report what changed, what was verified, and what is untested or unresolved.
