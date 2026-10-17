# codewhy Task Specification

> **This file is a work order for the assigned developer and their AI coding agent.**
>
> This task defines the scope of the assigned work. Do not expand the scope without explicit approval from the project lead.

---

## Task Information

**Task:** `Cache, incremental runs, resume, byte-identical re-runs`

**Task ID:** `CW-07` (index: `tasks/ALL-TASKS.md`)

**Depends on:** CW-01 through CW-06

**Branch:** `task/07-cache` (suggested; create only when the user wants a branch)

**Priority:** `Critical`

**Status:** `Not Started`

**Work log:** `logs/07-cache-incremental.md`

---

# 1. Goal

## Objective

Make re-running cheap and trustworthy. Deliverables:

1. **SQLite cache** (`.codewhy/cache.db`) with the single `pass_cache` table and `meta`.
2. **`input_key`** computation: a hash of everything that feeds a pass, plus pipeline version and config fingerprint.
3. **Cache integration at the single `run_pass` seam**: hits skip the provider; misses run and upsert immediately.
4. **Resume after interruption** (commit per result), orphan cleanup, corrupt-DB handling, `--full`.
5. **Persisted `generated_at`** so unchanged re-runs produce **byte-identical** output files.
6. The full cache scenario test table from `docs/TESTS.md`.

## Why This Exists

Without this, every run re-pays for the whole project, which makes the tool unusable as a "run after every coding session" habit and punishing on free-tier APIs. The design choice that matters: the key is a hash of the *inputs*, so the "smart cascade" (module re-runs only if member Purposes changed, project only if module Responsibilities changed) emerges from the rule instead of from separate invalidation logic that can drift out of sync.

---

# 2. Authoritative Documentation

* `CLAUDE.md`
* `docs/ARCHITECTURE.md` — *Layer 3 — Cache* (schema, the key rule and its inputs, cascade properties, caveats, other rules), *Timestamps and idempotence*, *Failure boundaries*, *Layer 4* (what each pass takes as inputs)
* `docs/TESTS.md` — *Layer 3 — Cache* (the scenario table), *Rendering* (idempotence)

---

# 3. Scope

## In Scope

* `src/codewhy/cache/{__init__,db,keys}.py`, `PIPELINE_VERSION` (constant), modifications to `pipeline.py` (`run_pass`) and `cli.py` (`--full`, `cached` counts), `.codewhy/.gitignore` creation.
* Tests in §8.

## Out of Scope

* Caching chunk calls individually (only the final file result is cached).
* Cache of rendered output, a cache CLI (`cache clear` etc.), compression, multi-process coordination beyond a clear error.
* `--dry-run` (task 08; expose a read-only lookup API for it now).
* Any new dependency (stdlib `sqlite3`, `hashlib`, `json` only).

Do not implement "while we're here" improvements outside this scope.

---

# 4. Allowed Files / Areas

```text
src/codewhy/cache/__init__.py
src/codewhy/cache/db.py
src/codewhy/cache/keys.py
src/codewhy/pipeline.py                       (run_pass + orchestration of key inputs)
src/codewhy/cli.py                            (--full, summary counts)
tests/test_cache_keys.py
tests/test_cache_db.py
tests/test_cache_scenarios.py
tests/test_idempotence.py
logs/07-cache-incremental.md
```

### May Modify If Required

```text
docs/ARCHITECTURE.md   (cache details this task forces — logged)
src/codewhy/summarize/*  (only to expose post-ladder inputs already planned in task 05)
```

---

# 5. Dependencies

| Dependency | Type | Notes |
|---|---|---|
| CW-05 `run_pass` seam, `ModuleRun/ProjectRun.prompt_inputs`, `PROMPT_VERSION` | Hard | |
| CW-06 `scan` and renderer | Hard | |
| stdlib `sqlite3` | Runtime | Locked. |

### Dependency Status

* [x] Dependency exists and is ready
* [ ] Dependency is being developed separately
* [ ] Dependency requires coordination
* [x] No external dependency

---

# 6. Contracts That Must Be Preserved

### Architecture (all from *Layer 3*)

* Tables: `meta(key, value)` with `schema_version`; `pass_cache(pass, subject, input_key, result_json, generated_at, PRIMARY KEY(pass, subject))`.
* `input_key = sha256(canonical_json({pass, PIPELINE_VERSION, config_fp, inputs}))`.
* `config_fp` = hash of provider kind, model, `context_tokens`, `max_output_tokens`, `temperature`, `seed`, `reasoning_effort`, `structured_output`. (`PROMPT_VERSION` from task 05 is folded into `PIPELINE_VERSION` handling: bumping either invalidates everything.)
* `inputs`:
  * **file:** path, full `source_text`, sorted `imports` paths, sorted `imported_by` paths.
  * **module:** module path; for each member `(path, Purpose text)` (the placeholder marker for failed members); the module's canonical relevant edges; sorted external dependency names — **as they were sent to the model after the overflow ladder** (use the `prompt_inputs` from task 05).
  * **project:** entry point or null; for each module `(path, Responsibility text)` post-ladder; the canonical project edge list; the other-file path list **as capped in the prompt**.
* A lookup is a hit iff the row for `(pass, subject)` has an equal `input_key`; anything else is a miss.
* `--full` ignores cache reads but still writes.
* `[generation failed]` results are **never** cached.
* Every upsert is committed immediately.
* After a completed run, rows whose subject is no longer a current file/module are deleted.
* Deterministic sections are rendered from current state on every run; the cache gates **only** LLM prose.
* Corrupt/unreadable DB or unknown `schema_version`: treat as empty, warn, rebuild; never crash.
* `generated_at` of each pass result is persisted and restored on a hit, so unchanged re-runs yield byte-identical outputs.

### Data conventions

* `canonical_json`: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`; hashing is SHA-256 hex of the UTF-8 bytes. Insertion order of dicts/lists-of-pairs never affects a key (sort pairs by path before hashing).

### Safety

* The cache stores only validated pass output (`result_json`) and timestamps — **not** prompts, source text, or API keys. `.codewhy/` gets a self-ignoring `.gitignore` containing `*` on creation so users do not commit it.
* The DB lives inside the scanned folder; nothing is written elsewhere.

---

# 7. Implementation Requirements

### Requirement 1 — DB layer (`cache/db.py`)

```python
class CacheDB:
    def __init__(self, path: Path): ...                  # lazy open
    def lookup(self, pass_name: str, subject: str, input_key: str) -> PassResult | None
    def store(self, pass_name: str, subject: str, input_key: str, result: PassResult) -> None   # commit immediately
    def delete_missing(self, pass_name: str, keep_subjects: set[str]) -> int
    def close(self) -> None
```

* `PRAGMA busy_timeout = 5000`; WAL is optional (justify in the log if you use it; plain rollback journal is fine for this scale). A lock timeout surfaces as `CacheLocked("Another codewhy run seems to be using <path>. Wait for it to finish or delete the lock.")` → exit 1.
* `schema_version` stored in `meta`; mismatch, "file is not a database", or corruption → rename nothing, log a warning ("cache unreadable, rebuilding"), recreate empty. A DB the process cannot write (read-only folder) → clear exit-1 error.
* Results serialized as JSON: `{"data": <model dict>, "metadata": {}, "generated_at": "<ISO UTC>"}`; `data` is revived into the correct pydantic model for the pass (`FilePass`/`ModulePass`/`ProjectPass`); a row that fails to revive is treated as a miss and overwritten.
* A read-only `peek(pass_name, subject, input_key) -> bool` (no side effects) for task 08's dry-run.
* Create `.codewhy/` and `.codewhy/.gitignore` (`*`) on first open for writing.

### Requirement 2 — Keys (`cache/keys.py`)

* `PIPELINE_VERSION: int` constant with a comment listing what requires a bump (prompt templates, schemas, extraction output shape, chunking behavior, ladder logic, `PROMPT_VERSION`).
* `config_fingerprint(config) -> str`, `file_key(...)`, `module_key(...)`, `project_key(...)`, plus `canonical_json` and `sha256_hex`. Each key function takes plain data (no live objects) so tests can drive them directly.
* Failed members enter module inputs as the marker string `"__failed__"` so a later success changes the key.

### Requirement 3 — Integration at `run_pass`

* `run_pass(pass_name, subject, key, fn)` looks up; on hit returns the stored `PassResult` (status `cached`); on miss calls `fn`, stores the result **only if it is not failed**, returns it. `--full` skips lookups.
* The orchestrator computes each key from the exact inputs in §6 *before* calling the pass; module/project keys use the post-ladder `prompt_inputs`. File results flow into module inputs as Purpose text (or the failed marker); module results flow into project inputs as Responsibility text.
* `on_item` now reports `cached`; `RunResult.counts["cached"]` is populated; the `scan` summary line shows cached vs generated counts.
* After a completed run: `delete_missing` for each pass with the current subjects. An interrupted run (exception/KeyboardInterrupt) performs **no** cleanup and leaves committed rows intact.
* `scan --full` forwards to the cache (reads skipped, rows rewritten).
* A cache I/O failure mid-run (e.g., disk full) degrades to uncached operation with a warning, never a crash and never a lost result.

### Requirement 4 — Idempotence

With no source/config change, a second `scan` makes **zero** provider calls and writes output files that are **byte-identical** to the first run's (timestamps come from persisted `generated_at`). Verify for single-file, docs-tree, and `both`.

### Error Handling

* Never fail a run because the cache is damaged; rebuild and continue.
* Never write a partial/failed model result to the cache.
* A key function must not raise on odd input (empty modules, missing entry point).

---

# 8. Testing Requirements

## Automated Tests

(Using `FakeProvider`, `fixed_clock`; module/file counts via the call log.)

* `test_cache_keys.py`: same inputs in different insertion order → same key; each input change changes the key (file source, imports, imported_by; module member Purpose, edges, externals; project entry point, module Responsibility, edges, other-file list); `config_fp` changes for each field in the list and for nothing else (e.g. `output.mode` does not change it); `PIPELINE_VERSION` bump changes every key; failed marker differs from any real text.
* `test_cache_db.py`: store/lookup round trip with correct model revival; miss on key change; row that fails to revive → miss and overwritten; `delete_missing`; garbage bytes in the DB file → warning, rebuild, usable; unknown `schema_version` → same; read-only directory → clear error; lock timeout → `CacheLocked` message; `.codewhy/.gitignore` created containing `*`; `peek` has no side effects.
* `test_cache_scenarios.py` — **every row of the table in `docs/TESTS.md` → Layer 3** with call counting:
  * no files changed → zero calls, byte-identical outputs;
  * source edited, identical Purpose text → file pass re-runs, module and project do **not**;
  * Purpose text differs → file + its module re-run; project only if that module's Responsibility text differs;
  * only `notes` changes → module not re-run;
  * import edge `A→B` becomes `A→C`, all texts identical → affected file prompts, relevant module keys, and project key change and re-run;
  * new file added to a module → module re-runs;
  * file deleted → row removed after the run; module re-runs if it had other members; an emptied module's row removed;
  * file renamed → old row removed, new created, neighbors' keys change;
  * entry point changes → only the project pass re-runs;
  * each `config_fp` field changes → everything re-runs;
  * `PIPELINE_VERSION` bump → everything re-runs;
  * `--full` → every pass calls the provider, rows rewritten;
  * `[generation failed]` not cached; next run retries it;
  * run interrupted after N upserts (FakeProvider raises on call N+1) → next run makes only the remaining calls (**resume**), and no cleanup ran for the interrupted run;
  * garbage DB / unknown schema / first-ever run → full generation without error.
* `test_idempotence.py`: two consecutive scans, no changes → identical bytes for single-file, docs-tree, both; zero provider calls on the second; changing one file changes only the documents behind the re-run passes (assert on timestamps); `generated_at` never equals wall-clock time of the second run.

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected result:

```text
all tests pass offline; the scenario table is fully covered by named tests
```

## Hardware / Model-Dependent Checks

Record each as Passed / Failed / Not run (reason) in `logs/07-cache-incremental.md`:

* Live: `scan` a real project twice with a real provider; the second run must report 100% cached and cost zero provider calls (check provider-side usage if available).
* Live: edit one file's logic, re-run; record which passes re-ran and whether that matches expectations (include the observed "identical Purpose text" rate, which indicates how much the cascade really saves).
* Live: interrupt a real run (Ctrl-C) midway; re-run and confirm it resumes.

## Manual Verification

1. Inspect `.codewhy/cache.db` with `sqlite3` (or Python): confirm it holds only result JSON and timestamps, no source text or keys.
2. Delete `.codewhy/` and re-run: a full regeneration with no errors.
3. Corrupt the DB file by hand and re-run: warning plus recovery.

---

# 9. Acceptance Criteria

* [ ] Keys are hashes of all and only the specified inputs; config/version changes invalidate.
* [ ] Hits skip the provider; misses store immediately; failures are never stored.
* [ ] Resume after interruption works; orphan rows are cleaned only after completed runs.
* [ ] Unchanged re-runs: zero calls and byte-identical outputs in all three output modes.
* [ ] Corrupt, unknown-version, read-only and locked DBs handled per §7 without crashes.
* [ ] Every scenario row in `docs/TESTS.md` Layer 3 is covered.
* [ ] `.codewhy/` self-ignores; cache holds no source text or secrets.
* [ ] Live checks recorded (or **Not run (reason)**), including the observed cascade savings.
* [ ] No new dependencies; `git diff` reviewed; work log updated.
* [ ] If a commit was authorized: staged diff reviewed, only intended files, no AI attribution, no push/PR.

---

# 10. Known Risks

* **Realistic savings are smaller than the scenario table suggests.** Unless the model returns identical text (temperature 0, fixed seed, local models), a source edit changes the Purpose and re-runs the module. Report observed savings honestly in the log and README; do not promise more.
* **A key that omits an input produces stale docs** — the failure mode users cannot see. The scenario tests are the defense; when a later change adds a prompt input, the matching key input must be added in the same change.
* **Module/project keys use post-ladder text.** If someone later changes ladder logic without bumping `PIPELINE_VERSION`, stale results survive; the comment on the constant must say so.
* **`generated_at` restoration** must preserve exact formatting (UTC, fixed precision) or "byte-identical" silently breaks.
* **SQLite on network drives / cloud-synced folders** can lock or corrupt; the rebuild-on-corruption path is the safety net.
* A cache row revived into a changed pydantic model (after a schema tweak) must be a miss, not a crash — covered by the revival test and by bumping `PIPELINE_VERSION` on schema changes.

---

# 11. Open Questions

* **requires clarification:** WAL mode or the default journal? — Recommended: default journal unless a concrete problem appears; log the choice.
* **requires clarification:** Should `scan` print the cache path and hit rate in the summary? — Recommended: only the cached/generated counts already in the summary line.

---

# 12. Handoff Notes

* Task 08's `--dry-run` uses `CacheDB.peek` and the key functions to report hits/misses; keep them side-effect free.
* Task 09's README must state the real caching behavior and its caveats (best case vs typical).
* Record in the log: schema/journal decisions, observed cascade savings, anything that required an architecture edit, and the `PIPELINE_VERSION` bump checklist.

If the task is complete:

> No outstanding handoff notes.

---

# 13. Definition of Done

See `CLAUDE.md` → *Definition of done*. Do not commit, push, open a PR, or merge unless the user explicitly authorizes that specific action.

## Final Agent Instruction

Before making changes: read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, this file, and the logs of tasks 01–06; run `git status`; write your plan into `logs/07-cache-incremental.md`.

During implementation: derive every key from plain data; touch `run_pass` and the orchestrator only as described; stay in scope; test the whole scenario table.

If the task requires something not covered here, stop and ask the project lead.

Before handing off: run the tests, run the live checks you can (record the rest as Not run), review `git diff` and `git status`, update the log, and report what changed, what was verified, and what is untested or unresolved.
