# CW-08 — CLI completeness and robustness

Branch: `task/08-cli-robustness`, created from `review/06-07-render-cache`
(commit `020635f`: CW-01–CW-07 plus the CW-06/07 review).

## 2026-10-22 — plan

Starting point: `pytest -q` 751 passed, 1 skipped.

Already present from earlier tasks:
- `scan` exits 130 on Ctrl-C, but with a different message;
- overwrite checks run before the pipeline;
- the cache offers `peek` and resume.

Missing:
- `--dry-run`;
- `rich` progress;
- atomic writes;
- an exit-code function;
- the failure matrix;
- the `CODEWHY_DEBUG` internal-error path;
- skip lines for files that fail to parse;
- collected warnings.

Order of work:

1. `render/writer.py`: `write_atomic` (temp file in the same folder, fsync,
   `os.replace`) and stale-temp cleanup. `docs_tree.write_outputs` (the task
   06 writer) uses it.
2. `progress.py`:
   - `ProgressReporter` (TTY: rich spinner plus item lines; non-TTY: plain
     lines; `--quiet`: nothing);
   - terminal text with ASCII fallback;
   - the summary line;
   - `exit_code_for`.
3. `plan.py`: `plan_scan(project, config, cache)` for `--dry-run`, plus
   rendering as a rich table on a TTY and plain aligned text otherwise.
4. `cli.py`:
   - `--dry-run`;
   - reporter wiring;
   - warnings collected from library loggers and printed before the summary;
   - skip lines for discovery and extraction skips;
   - the interrupt message;
   - stale-temp cleanup before discovery;
   - an internal-error guard with `CODEWHY_DEBUG=1`;
   - the "expected a folder" message.
5. Tests: the six modules listed in task §8.
6. Live checks with local Ollama, as far as possible.

## Decisions

- **The writer stays in `render/docs_tree.py`.** Task 06 put the overwrite
  checks and stale-doc cleanup there, so only the atomic primitive and temp
  cleanup go in the new `render/writer.py`.
- **Temp file name:** `<name>.<16 hex>.tmp` in the target's folder. Cleanup
  removes only files that match exactly that pattern, in the project root
  (the single file's folder) and inside the docs-tree folder, never through
  symlinks.
- **Cleanup timing:** stale temps are removed after preflight and before
  discovery. A leftover temp would otherwise show up under "Other files" and
  change the project cache key. `--dry-run` never deletes anything.
- **Progress output:** the task 06 stage and per-request lines
  (`discovery: started`, `file request …`, `checking provider …`) are not in
  the architecture's progress contract.
  - On a TTY they become the rich spinner's status text, which still shows
    long local-model waits.
  - Non-TTY output is only item lines, warnings, skip lines and the summary.
  - The task 06 tests that pinned those lines are replaced.
- **Item line:** `[i/N] <pass>  <subject>  (<status>)`, as in the
  architecture example. The project item's subject is the project folder
  name.
- **Summary line:** the token count gets thousands separators (`≈ 9,400
  tokens`), as in the architecture example.
- **Skip lines** now include files that fail to parse (extraction skips) as
  `skipped <path> (<warning>)`. The warning names the error and line, never
  source text.
- **Dry run, module and project passes:** their keys depend on Purpose and
  Responsibility texts that only a full cache read could recover. The
  contract allows only `peek`, so their hits are reported as unknown ("—")
  and their calls count every item: an upper bound, labelled as such.
  Tokens come from prompts assembled with maximum-length placeholder texts
  (the schema caps), through the real overflow ladder.
- **Dry run, chunked files:** the call count (chunks + 1 combine) is exact.
  The combine prompt depends on the chunk Purposes, so its tokens use the same
  maximum-length placeholders; the output says so when chunked files exist.
- **Dry run, exit code:** 0 when a plan is printed; 1 for the fatal cases
  (bad config, no supported files).
- **`pipeline.config_fingerprint_for(config)`:** the config fingerprint
  computation moves out of `_run_pipeline` into a public function, so the dry
  run computes identical file keys. Pure refactor, no behavior change.
- **Pre-write writability check.** `docs_tree.write_outputs` now refuses
  before the first write when a target folder is not writable
  (`os.access`), so `both` mode cannot write `PROJECT_DOCS.md` and then fail
  on `codewhy-docs/`. This is best effort, because Windows ACLs can make
  `os.access` optimistic; the write itself still reports any error.
- **Closed pipe** (`codewhy scan … | head`): exits 1 quietly instead of
  reporting an internal error.
- **Terminal output from `progress.py`:** `progress.py` (named by the task)
  writes to the terminal only through the `echo` and rich `Console` that
  `cli.py` passes in. The library modules still never print.
- **Architecture edits** (CLI surface, Overwrite protection):
  - exit 130 and its message;
  - the internal-error / `CODEWHY_DEBUG` behavior;
  - collected warnings;
  - dry-run details (unknown module/project hits, placeholder-based token
    upper bounds, remote hint);
  - TTY / non-TTY progress and the ASCII fallback;
  - atomic writes and temp cleanup.

## Implementation

- New:
  - `src/codewhy/render/writer.py`;
  - `src/codewhy/progress.py`;
  - `src/codewhy/plan.py`.
- Changed:
  - `src/codewhy/cli.py` (scan rewrite, `--dry-run`, guard on every
    command, `say()` with the ASCII fallback, "expected a folder" message);
  - `src/codewhy/render/docs_tree.py` (atomic writes, writability
    pre-check);
  - `src/codewhy/pipeline.py` (`config_fingerprint_for` only);
  - `docs/ARCHITECTURE.md`.
- Tests:
  - new: `test_dry_run.py` (10), `test_progress.py` (8),
    `test_exit_codes.py` (13), `test_failure_matrix.py` (14 rows),
    `test_atomic_write.py` (9), `test_cli_edge_cases.py` (11);
  - `test_scan_integration.py`: the two task 06 stage-line tests are removed
    (their contract is replaced by `test_progress.py`), and the interrupt
    message, the skip lines and the token formatting are updated.

### Failure matrix: every row reproduced as specified

All 14 cases pass without changing the architecture table.

| Row | How it is driven |
|---|---|
| Provider unreachable | Real Ollama provider behind `httpx.MockTransport` |
| Ollama model not pulled | Real Ollama provider behind `httpx.MockTransport` |
| Remote not acknowledged | Real Gemini provider behind `httpx.MockTransport`; zero HTTP requests |
| Grammar missing | `_language_for` monkeypatched to fail |
| Malformed .py / .js / .ts | FakeProvider |
| Too large / undecodable | FakeProvider |
| Cache garbage / unknown schema | FakeProvider |
| LLM output malformed | Real Ollama provider: one retry (2 requests), then `[generation failed]`, not cached |
| Transient error | Real Ollama provider: request + 2 backoff retries (3 requests), then placeholder |
| Output not codewhy's | FakeProvider; zero calls, tree unchanged |
| Function larger than chunk | Truncation marker sent; exit 0 |

The only expectation corrected was my own test text. The consent message is
"Your source code will be sent to <host>." on stderr (task 04's wording),
not the literal "…to this endpoint".

### Final exit-code table

| Code | When |
|---|---|
| 0 | Everything documented (ignored/unsupported files don't count); `--dry-run` printed a plan |
| 1 | Fatal: bad config, path missing or a file, provider unreachable, model missing, remote not acknowledged, refused overwrite, auth/bad-request abort, no supported files, unwritable output, cache locked or unwritable, internal error |
| 2 | Completed, but ≥1 file skipped (parse/size/decode/unreadable) or ≥1 `[generation failed]` |
| 130 | Interrupted; cache rows kept, previous output untouched |

## Verification

- `pytest -q`: **814 passed, 1 skipped** (the skip is the live E2E). Start
  of task: 751 passed.
- `git diff --check`: clean. No unused imports in new or changed files
  (AST check).
- Mutation checks, each restored byte for byte from a scratchpad backup. All
  14 were caught:
  - non-atomic write;
  - temp file kept on failure;
  - no stale-temp cleanup;
  - exit code ignores failures;
  - dry run builds a provider;
  - chunked file counted as one call;
  - plan ignores the cache;
  - guard leaks exception text;
  - no ASCII fallback;
  - no writability pre-check;
  - extraction skips not printed;
  - old interrupt message;
  - overwrite check after the pipeline;
  - warnings not collected.

### Live / manual checks

Local Ollama `qwen2.5-coder:7b`, macOS, a scratchpad copy of Sudoku Solver
(one project, as the lead instructed).

| Check | Result |
|---|---|
| `--dry-run` vs real run call counts | **Passed** with a local provider; **hosted provider not run** (no API key in this environment). Cache cleared, dry run predicted 9 file calls (exact) + 3 (upper bound) = 12. Interrupted run + resumed run made exactly 12 `/api/chat` requests (Ollama server log), 9 of them file passes. |
| Ctrl-C midway, message, 130, resume | **Passed.** SIGINT after 4 items: exit 130, stderr "Interrupted. Progress is saved in the cache; re-run to resume.", previous `PROJECT_DOCS.md` byte-identical, no `.tmp` left. Re-run: 4 cached, 8 generated. |
| Real run in a terminal (macOS) | **Passed** under a pseudo-TTY (`script`): rich spinner line with item lines above it, then the summary; the dry-run table renders with right-aligned numbers. |
| Real run on Linux / Windows Terminal / PowerShell | **Not run** (no such machine here; task 09 CI). ASCII fallback is covered by tests only. |
| Non-TTY (`\| cat`) | **Passed**: plain item lines and summary, no control codes; `\| head` exits quietly. |
| Bad model / wrong host / dummy `GEMINI_API_KEY` / unset key | **Passed**. Each prints one `error:` line with the fix: `ollama pull …`; `ollama serve`; "rejected the key in GEMINI_API_KEY (HTTP 400) …"; "The environment variable GEMINI_API_KEY is not set …". The dummy-key check sent only the preflight model-list request, no source. |
| `kill -9` mid-write | **Not run** (cannot be timed reliably by hand). Covered by tests: a failure or interrupt between temp write and replace keeps the previous file, and stale temps are removed on the next scan. |

## Open issues for the lead

- The dry run cannot report module/project cache hits with `peek` alone:
  their keys need the cached Purpose/Responsibility texts. For an unchanged
  re-run it therefore shows "up to N" module/project calls while the real
  run makes 0. Exact numbers would need a read-only result lookup on
  `CacheDB`. That is a small `cache/db.py` addition, but outside this task's
  allowed files.
- Atomic writes are per file. A docs-tree run killed between files can leave
  a mix of old and new documents until the next run, as the task's Known
  Risks say. The README (task 09) should say so.
- The task 06 stage/request lines now appear only in the TTY spinner. If CI
  users want heartbeat lines for slow local models, that would be a new
  contract.
