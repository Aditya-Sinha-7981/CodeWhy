# Task 02 — Extraction Work Log

## 2026-10-19 — plan

Task 01 is not present in this worktree: there is no `pyproject.toml`, `src/codewhy` package scaffold, or shared test infrastructure. Task 02 will therefore use test-local `SourceFile` doubles and run focused tests with `PYTHONPATH=src`, without creating a production `SourceFile` fallback or changing Task 01 interfaces.

Implementation order:

1. Immutable extraction models and canonical serialization.
2. Python AST extraction and tests.
3. Tree-sitter grammar loading and JavaScript/TypeScript extraction and tests.
4. Fixtures and broken-file builder.
5. Dispatcher, determinism, ordering, and failure-boundary tests.
6. Wheel-check script and GitHub Actions workflow.
7. Local checks, manual checks where possible, and handoff review.

Task 01 responsibilities remain file reading, decoding, BOM/CRLF normalization, size limits, symlink handling, and production `SourceFile` creation. Task 02 extractors consume normalized text only.

No architecture documentation changes are planned or authorized.

## Checks

Local focused checks and their exact results will be recorded here. The OS/Python wheel matrix requires GitHub Actions; if no remote is available it will be recorded as Not run (no remote).

### 2026-10-19

- `D:\Minor Project\CodeWhy\.venv\Scripts\python.exe -m pytest tests/test_python_ast.py -q` — Not run: pytest is not installed in the existing virtual environment; no installation was performed.
- `D:\Minor Project\CodeWhy\.venv\Scripts\python.exe -m compileall -q src tests scripts` — Passed.
- `D:\Minor Project\CodeWhy\.venv\Scripts\python.exe scripts/check_wheels.py` — Passed. Installed versions: tree-sitter 0.26.0, tree-sitter-javascript 0.25.0, tree-sitter-typescript 0.23.2.
- Direct offline extraction assertions over committed fixtures — Passed. Python, JavaScript, TypeScript, JSX, and TSX fixtures parsed successfully; TypeScript declarations and literal/non-literal import behavior were checked.
- Focused pytest suite — Not run: pytest is unavailable locally and installing dependencies was explicitly not authorized.
- GitHub Actions wheel matrix — Not run: no GitHub access/remote was available in this environment.
- `D:\Minor Project\CodeWhy\.venv\Scripts\python.exe -m compileall -q src tests scripts` — Passed on final review.
- Final allowed-file review — Passed: no `pyproject.toml` or `docs/ARCHITECTURE.md` change; the pre-existing untracked `requirements.txt` was not modified.

## Open issues

- Task 01 integration remains pending.
- `pyproject.toml` and dependency metadata are intentionally deferred until Task 01 establishes them.

## Review status

Implementation is awaiting Task 01 integration and the user's review. Task 02 is not claimed complete because the focused pytest suite could not run locally.

## 2026-10-19 — targeted correction pass

Corrections made within the approved Task 02 files:

- Anonymous default-exported arrows are extracted as `default`.
- Multiple variable declarators retain their own syntax node and signature; one statement produces one unit.
- Nested JSDoc uses absolute source-byte line boundaries.
- JavaScript import and export bindings preserve each binding/specifier as written, including aliases.
- Grammar loading failures are separated from parser/runtime failures.
- Dispatcher warnings are logged at warning level with traceback details at debug level.
- Definition-free JavaScript/TypeScript modules use the project-relative filename as the module unit name.
- Unsupported symlink creation emits a warning containing the platform error.
- Regression tests cover the corrected parser cases, supported extensions, recursive child adjacency, and serialization isolation.

Checks:

- `D:\Minor Project\CodeWhy\.venv\Scripts\python.exe -m compileall -q src tests scripts` — Passed (exit 0).
- `D:\Minor Project\CodeWhy\.venv\Scripts\python.exe scripts/check_wheels.py` — Passed (exit 0); versions 0.26.0, 0.25.0, 0.23.2.
- `D:\Minor Project\CodeWhy\.venv\Scripts\python.exe -m pytest tests/test_python_ast.py tests/test_ts_treesitter.py tests/test_extraction_units.py tests/test_extraction_dispatch.py -q` — Not run: pytest is not installed; no installation performed.
- Direct offline regression assertions and committed-fixture parsing — Passed.
- GitHub OS/Python wheel matrix — Not run (no remote/GitHub access).
- Lazy grammar imports were added so missing grammar packages can be converted to the required grammar-unavailable skipped result instead of failing at module import time.
- Final correction-pass command (`compileall`, `check_wheels.py`, and targeted smoke assertions) — Passed; all three exit codes were 0. A first attempt had only a PowerShell `Math.Max` aggregation error after the checks; it was rerun with a valid exit aggregation.

Remaining review items: pytest execution, Task 01 integration, architecture `types` documentation, discovery-owned undecodable/oversized checks, and GitHub matrix verification.

## 2026-10-19 — focused pytest follow-up

- Initial focused run: `F....F.....................` — 25 passed, 2 failed. Both failures were incorrect test expectations: Python `ast.unparse()` emits `int=1`, and the test source uses `export { helper }`, whose imported binding is `helper`.
- Corrected assertions only in `tests/test_python_ast.py` and `tests/test_ts_treesitter.py`.
- Final focused run: `D:\Minor Project\CodeWhy\.venv\Scripts\python.exe -m pytest tests/test_python_ast.py tests/test_ts_treesitter.py tests/test_extraction_units.py tests/test_extraction_dispatch.py -q` — Passed: 27 passed in 0.09s.
