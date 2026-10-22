# Review log — CW-07 cache, incremental runs, resume, idempotence

Branch: `review/06-07-render-cache`, created from `task/07-cache` (commit
`875d2b0`, on top of `main` at `7bb53ee`, which merged CW-06). The same
branch also carries the remaining CW-06 checks (see
`logs/review-06-render-scan.md`). Reviewer: Aditya (lead). The author's log,
`logs/07-cache-incremental.md`, is left unchanged.

## 2026-10-22

### Review findings (before any change)

Checked against `docs/ARCHITECTURE.md` (*Layer 3 — Cache*, *Timestamps and
idempotence*, *Failure boundaries*, *CLI surface*), `docs/TESTS.md`
(*Layer 3 — Cache*, *Rendering*, *E2E*) and `tasks/07-cache-incremental.md`.

- Baseline: `pytest -q` **711 passed, 1 skipped** (Python 3.14.7, macOS).
- Works as specified (read and run):
  - SQLite schema (`meta`, `pass_cache`);
  - canonical JSON and SHA-256 keys;
  - `config_fp` over exactly the eight listed fields;
  - module keys use the post-ladder `prompt_inputs`, with the `__failed__`
    marker for failed members;
  - failed results are never stored, and every upsert commits;
  - orphan cleanup runs only after a completed run;
  - `generated_at` is persisted at the renderer's precision (seconds, UTC);
  - `.codewhy/.gitignore` contains `*`;
  - `peek` is read-only;
  - `--full` skips reads;
  - three-mode byte-identical re-runs.

Defects found:

1. **The scenario table is mostly untested** (acceptance criterion: "Every
   scenario row … is covered"). `test_cache_scenarios.py` has 4 tests for 17
   rows. Missing:
   - identical Purpose → module/project not re-run;
   - Purpose differs → cascade;
   - notes-only change;
   - edge change;
   - new file, deleted file (module re-run, emptied module row), rename;
   - entry point change;
   - each `config_fp` field;
   - `PIPELINE_VERSION` bump;
   - garbage DB, unknown schema and first run end to end.

   The resume test does not check that *only the remaining* calls are made.
   `test_idempotence.py` lacks "one file changed → only the documents behind
   re-run passes change" and "`generated_at` ≠ second run's wall clock".
   `test_cache_db.py` lacks:
   - the rebuild warning;
   - a real read-only directory;
   - revival of module/project models;
   - "peek has no side effects".
2. **Read-only/locked cache found only after a paid call.** The DB is opened
   lazily on the first `store`, which runs after the first provider call
   returns. A read-only project folder therefore costs one LLM call and then
   exits 1 without output. Req. 1 asks for a clear exit-1 error. Opening the
   cache before the first pass makes that error fail fast.
3. **A cache error mid-run can lose a result.** `run_pass` re-raises
   `CacheLocked` (and lets `CacheReadOnly` through) from `store` after the
   provider call. That aborts the run and drops the paid result, which
   contradicts Req. 3 ("degrades to uncached operation with a warning, never a
   crash and never a lost result").
4. **The project key leaves out a prompt input.** The project prompt includes
   `omitted` (modules the ladder dropped), but `project_key` does not hash it.
   The module key does hash it. Both keys must follow the same post-ladder
   rule.
5. **The summary line doesn't match the architecture.** The architecture's
   *CLI surface* gives `18 files, 11 cached, 7 generated, 0 failed, 1
   skipped.`; the code prints `… generated, … cached, …`.
6. **The commit message overstates what was run.** `875d2b0` says "Manual
   checks confirmed zero-call cache hits and byte-identical outputs", but the
   author's log records every live and manual check as **Not run**. Only the
   automated tests support that claim.

Not changed (noted):

- `run_pass(pass_name, subject, inputs, fn)` keeps task 05's signature. The
  key travels inside a private `_CacheCall` rather than as the third argument
  that Req. 3 names. It works, and it keeps the task 05 seam tests
  (`test_pipeline.py`) meaningful. Changing the signature is churn without a
  behavior gain.
- `root_name` (the folder name) is in the project prompt but not in the
  project key, because the architecture's input list omits it. Renaming the
  project folder therefore reuses a project overview written with the old
  name. This is a contract question for the lead (see open issues), not
  changed here.
- The CLI recomputes the docs-tree target paths to validate them before the
  pipeline runs. The computation duplicates `render_docs_tree` and matches it
  today. A shared helper would prevent drift, but that is a cleanup, not a
  defect.

### Plan

1. Fail fast: open and validate the cache at pipeline start (before any
   provider call); lock/read-only errors there stay fatal (exit 1).
2. Mid-run, every cache error degrades to uncached operation with a warning;
   the provider result is always kept.
3. Hash the project prompt's `omitted` in `project_key`.
4. Summary line order per the architecture; update the scan-integration
   expectations.
5. Tests: every scenario row as a named test, plus the missing key, DB and
   idempotence tests.
6. Live checks with the local Ollama (`qwen2.5-coder:7b`, the only coder
   model pulled here): two scans, a one-file edit, a Ctrl-C resume.

### Fixes (this branch)

1. `pipeline.py`: the cache is opened and validated (`ensure_open`) before
   the first pass.
   - A locked or unwritable cache is a fatal exit 1 with zero provider calls.
   - A corrupt or unknown-schema DB still rebuilds with a warning.
2. `pipeline.py` `run_pass` and orphan cleanup: after the open, any
   `CacheError` (I/O, lock, read-only) disables caching for the rest of the
   run with a warning. The provider result is always kept.
3. `cache/keys.py` `project_key` hashes the ladder's `omitted` count, as
   `module_key` already did.
   - `docs/ARCHITECTURE.md` *Layer 3* now says that module and project texts
     are keyed as sent after the ladder, including the omitted count.
     **Architecture edit.**
4. `cli.py` summary line order now follows the architecture: `N files, C
   cached, G generated, F failed, S skipped. …`. The scan-integration
   expectations were updated to match.
5. Tests:
   - `test_cache_scenarios.py`: rewritten with one named test per TESTS.md
     row (31 tests), counting calls per (pass, subject).
   - `test_cache_db.py`: +6 tests (module/project revival, wrong-shape row,
     failures never stored, `peek` side effects, read-only folder before any
     call, CLI exit 1).
   - `test_cache_keys.py`: +5 tests.
   - `test_idempotence.py`: +2 tests (only re-run documents change, with
     timestamps; `generated_at` comes from the cache, not the second run's
     clock).
6. `test_scan_integration.py::test_auth_failure_writes_nothing` now asserts
   that no output files are written (task 06 §8 wording), not that the tree
   is unchanged. Opening the cache first can leave an empty, self-ignoring
   `.codewhy/` on a first-run auth failure. Trade-off: this is preferred
   over paying for a call before discovering a read-only cache.

### Verification

- `pytest -q`: **751 passed, 1 skipped** (the skip is the live E2E).
- Mutation checks over the cache tests, each restored and compared byte for
  byte with a scratchpad backup. All caught:
  - module key ignores Purpose;
  - file key ignores `imported_by`;
  - project key ignores entry point;
  - `config_fp` drops `seed`;
  - lazy cache open;
  - mid-run `CacheError` aborts;
  - `generated_at` not restored;
  - no orphan cleanup;
  - `--full` still reads;
  - failed results stored (both guards removed; removing one alone is an
    equivalent mutant, because `PassResult` forbids data + failure).

### Live / manual checks (task §8), local Ollama `qwen2.5-coder:7b`, macOS, 24 GB

By instruction from the lead, only one real project was used.

| Check | Result |
|---|---|
| Scan a real project twice | **Passed**: Sudoku Solver (copy), 9 files. Run 1: 12 generated, 54 s. Run 2: 12 cached, 0 generated, 0.3 s, `PROJECT_DOCS.md` byte-identical. |
| Edit one file's logic, re-run | **Passed**, matches the rules. Two edits, each re-ran 3 of 12 passes: `constants.py` → file, module `.`, project; `test/test2.py` → file, module `test`, project. Identical-Purpose rate: **0 of 2**. The 7B model at temperature 0 rewords the Purpose after any edit, so the realistic saving is "only the edited file's module chain re-runs", not "only the file". |
| Ctrl-C midway, re-run resumes | **Partly run.** lineaLED CRM copy, SIGINT after 5 of 45 items: exit 130, "scan interrupted; no output was written.", 5 rows committed, no output file. The resuming re-run was **Not run** (stopped by the lead for time). Resume itself is covered offline by `test_interrupted_run_resumes_with_only_the_remaining_calls`. |
| Inspect `cache.db` | **Passed** (via the scenario tests' direct reads): rows hold `result_json` and timestamps only. |
| Delete `.codewhy/` / corrupt DB by hand | Covered by automated first-run / garbage-DB scenario tests; not repeated by hand. |

### Open issues for the lead

- **`root_name` is not in the project key.** Renaming the project folder
  reuses the old overview. Decide whether to add it (architecture edit plus
  `PIPELINE_VERSION` bump).
- The CLI duplicates `render_docs_tree`'s path layout for the pre-pipeline
  overwrite check; a shared helper would prevent drift.
- `run_pass` keeps the task 05 `(pass, subject, inputs, fn)` signature
  instead of Req. 3's `key` argument (see above).
