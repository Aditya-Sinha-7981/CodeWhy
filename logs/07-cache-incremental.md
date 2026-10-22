# CW-07 — Cache, incremental runs, resume, and idempotence

## 2026-10-21 — implementation plan and decisions

Implementation order:

1. Add the stdlib SQLite cache package, schema validation/rebuild behavior,
   result revival, and canonical key helpers.
2. Integrate cache lookup/store/status handling at the existing `run_pass`
   seam and derive file, module, and project keys from the exact Task 7 inputs.
3. Add `--full`, cached summary counts, completed-run orphan cleanup, and
   interruption-safe cache lifecycle handling.
4. Add offline key, database, scenario, and idempotence tests, then run the
   focused tests and full suite.

Decisions:

- Stay on the current `task/06-render-scan` worktree because branch switching
  was not separately authorized. The requested Task 7 branch is
  `task/07-cache`; no branch switch is performed in this session.
- Use SQLite's default rollback journal; no WAL mode.
- Keep `PassResult` unchanged and keep `metadata == {}`. Cached/generated
  status is carried by a private pipeline execution-status mechanism.
- `PIPELINE_VERSION` is the single cache version input. Its bump checklist
  covers prompt templates, schemas, extraction output shape, chunking,
  overflow-ladder behavior, and `PROMPT_VERSION`.
- Keep `root_name` out of the project cache key as required by Task 7. The
  full canonical project edge list is included separately from the capped
  project prompt inputs.
- Corrupt or unknown-schema databases are closed, the exact `cache.db` file
  is unlinked, and a new database is created in the same `.codewhy` folder;
  no rename or recursive deletion is used.
- Lock timeout and read-only errors remain fatal; recoverable cache I/O errors
  after startup degrade to uncached operation with a warning.

## Scope

Files are limited to the Task 7 work order: `src/codewhy/cache/*`,
`pipeline.py`, `cli.py`, the four named cache/idempotence test modules, and
this work log. `docs/ARCHITECTURE.md` is unchanged unless implementation
reveals a contract that genuinely requires approval.

## Verification record

Automated and manual checks will be appended here with their exact commands
and Passed / Failed / Not run status. No live provider or real-project check
is claimed until it is run.

## Verification

- **Passed** — `.venv\Scripts\python.exe -m pytest -q --basetemp .test-tmp`
  — 704 passed, 7 skipped, 2 Windows symlink warnings.
- **Passed** — focused cache keys, SQLite DB, scenarios, idempotence, and scan
  integration tests — all passed after the final summary/cache-boundary fixes.
- **Passed** — `git diff --check` (Git emitted only expected CRLF normalization
  warnings for existing Windows-working-tree files).
- **Not run (reason)** — live scan twice with a real provider; no real project
  and provider credentials were authorized for this session.
- **Not run (reason)** — live edit-one-file cascade measurement; requires a
  real provider and project.
- **Not run (reason)** — live Ctrl-C/resume run; requires a real provider and
  project.
- **Not run (reason)** — manual sqlite inspection, delete-cache regeneration,
  and hand-corruption CLI checks; automated corruption/rebuild coverage passed,
  but no separate user-project filesystem check was authorized.
