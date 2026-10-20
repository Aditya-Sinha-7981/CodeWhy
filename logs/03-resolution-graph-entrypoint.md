# Task 03 — Resolution, Graph, Entry Point, and Inspect Work Log

## 2026-10-20 — plan

Read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`,
`tasks/03-resolution-graph-entrypoint.md`, and the Task 01 and Task 02 work
logs before implementation. The worktree was clean.

Implementation order:

1. Write and run flat-script Python resolver tests before implementing the resolver.
2. Implement and test Python import resolution.
3. Implement and test JavaScript/TypeScript import resolution.
4. Implement and test graph construction and query helpers.
5. Add and test the nested integration fixture.
6. Implement and test entry-point detection.
7. Integrate extraction, graph construction, and entry-point detection into
   `ProjectModel` and `build_project`.
8. Implement and test the hidden `inspect` command.
9. Preserve the existing `scan` output and verify compatibility.
10. Run the complete local verification suite, inspect the final diff, and
    document manual and CI-only checks.

Scope constraints:

- Only Task 03 allowed files may change.
- `requirements.txt`, `docs/ARCHITECTURE.md`, Task 01 files, and
  `tests/test_cli_init_config.py` remain untouched.
- `pyproject.toml` may receive only the `networkx` dependency.
- No scanned source code is executed.
- No commits, pushes, pull requests, or merges will be made.

Known specification conflict:

The Task 03 work order mentions scan edge-count reporting, while the existing
Task 01 test asserts the current scan output exactly. The current scan output
and `tests/test_cli_init_config.py` will be preserved. Any change to that
contract or out-of-scope test requires separate approval.

## Decisions and implementation notes

## 2026-10-20 — targeted malformed-input correction pass

- Re-read the governing Task 03 documents and current implementation before
  changing code.
- Corrected Python resolver defensive handling for invalid `imp.level` and
  malformed module metadata so the fallback cannot raise again.
- Corrected JavaScript/TypeScript resolver handling for malformed module values,
  including `imp.module is None`.
- Added focused regression tests for invalid Python levels, malformed Python
  modules, and malformed JavaScript modules.
- `python -m pytest tests/test_python_resolver.py tests/test_js_resolver.py -q`
  — Passed: 23 tests in 0.16s.
- `git diff --check` — Passed (exit 0).
- No dependencies were installed. No broader Task 03 tests were run in this
  pass, as requested.

## 2026-10-20 — focused failure correction pass

- Verified the original Task 03 and architecture contracts: module canonical
  edges include every edge where either endpoint belongs to the selected module.
- Corrected the graph test expectation to retain `c.py -> b.py`; the graph
  implementation was not weakened.
- Corrected the inspect creation-order test to compare all substantive JSON
  fields while allowing the naturally different `root_name` values for the
  `first` and `second` temporary projects.
- The initial affected-test run reported the expected graph assertion failure;
  the inspect test was blocked by a Windows permission error creating pytest's
  default temporary directory.
- `.venv\Scripts\python.exe -m pytest --basetemp .pytest-task03 tests/test_graph.py::test_graph_queries_and_module_derived_data_are_sorted tests/test_inspect.py::test_inspect_is_independent_of_file_creation_order -q` — Passed: 2 tests.
- Process-local writable-temp run of `.venv\Scripts\python.exe -m pytest -q` — Passed: 220 tests, 3 skipped, 1 expected Windows symlink-privilege warning.
- `git diff --check` — pending final command after this log update.

- Resolvers will be pure and will use discovered project paths rather than
  filesystem access.
- Graph nodes will include only successfully extracted supported files.
- Inspect output will be deterministic, read-only, offline, and source-free.
- Any deviations, unresolved requirements, command results, and blocked checks
  will be appended here during implementation.

## 2026-10-20 — phases 1–2

- Added the flat-script Python resolver tests before implementing the resolver,
  as required by the task.
- The pre-implementation command `python -m pytest tests/test_python_resolver.py -q`
  failed during collection because `codewhy.graph` did not yet exist.
- Added pure Python resolution models and resolver logic without filesystem
  access.
- Expanded coverage for flat scripts, relative levels, package fallback,
  submodule preference, `src/` roots, longest-prefix matching, root precedence,
  external imports, unresolved relative imports, and odd input.
- `python -m pytest tests/test_python_resolver.py -q` — Passed: 11 tests.

## 2026-10-20 — phase 3

- Added the JavaScript/TypeScript resolver and focused coverage for extension
  order, indexes, exact extensions, TypeScript ESM fallback, rooted imports,
  bare/scoped/alias externals, non-source assets, and root escapes.
- The first JS resolver run reported one failure: `@/utils` was classified as a
  scoped package. The classifier was corrected so the alias is external with
  top-level name `@`.
- Added the required mixed `from . import x, y` fallback coverage and corrected
  the Python resolver to union found submodules with the package initializer.
- `python -m pytest tests/test_js_resolver.py -q` — Passed: 8 tests.
- `python -m pytest tests/test_python_resolver.py tests/test_js_resolver.py -q`
  — Passed: 20 tests.

## 2026-10-20 — phase 4

- Added `networkx` as the only new project dependency.
- Added graph construction and deterministic query helpers covering successful
  nodes, skipped files, language-specific resolution, cycles, self-edge
  removal, canonical edges, module subsets, rankings, module indegree, and
  external dependency unions.
- `python -m pytest tests/test_graph.py -q` — Not run to completion: test
  collection failed with `ModuleNotFoundError: No module named 'networkx'`.
  Package installation is prohibited by the approved constraints.
- The graph implementation was added without changing any out-of-scope files;
  compile-only validation will be included in the next local check.

## 2026-10-20 — phases 5–8

- Added `tests/fixtures/nested_modules/` with a `src/` Python package,
  subpackages, re-exports, relative level-one and level-two imports, a cycle,
  a popular utility, standard-library and third-party imports, a JS/TS subtree,
  a folder containing only subfolders, and root `[project.scripts]` metadata.
- Added entry-point detection for explicit configuration, root TOML metadata,
  root package JSON metadata, supported start commands, and conventional paths.
- Extended `ProjectModel` and `build_project` to run Task 02 extraction, graph
  construction, and entry-point detection without network access.
- Added hidden `codewhy inspect` output with deterministic JSON, graph queries,
  skips, modules, and source-free file structures. The existing scan output was
  not changed.
- Added focused graph, entry-point, and inspect tests.
- `python -m compileall -q src/codewhy/graph src/codewhy/entrypoint.py src/codewhy/project.py tests/test_python_resolver.py tests/test_js_resolver.py tests/test_graph.py tests/test_entrypoint.py` — Passed.
- TOML parsing check for `pyproject.toml` — Passed.
- Runtime tests for graph, entry point, and inspect remain blocked by missing
  `networkx`; installation is prohibited. No dependency installation was run.

## 2026-10-20 — verification update

- python -m pytest tests/test_python_resolver.py tests/test_js_resolver.py -q — Passed: 20 tests in 0.09s.
- python -m pytest -q — Blocked during collection: `networkx` is not installed; 8 modules failed with ModuleNotFoundError. No installation was attempted.
- python -m compileall -q src tests scripts — Passed (exit 0).
- python scripts/check_wheels.py — Passed (exit 0); tree-sitter versions reported 0.26.0, 0.25.0, and 0.23.2.
- python -m codewhy inspect tests/fixtures/nested_modules — Blocked during import by missing `networkx` (exit 1).
- Manual inspection of two real projects — Not run: the inspect command cannot start without `networkx`; no GitHub access or CI matrix execution was used.
- No architecture documentation, Task 01 files, `requirements.txt`, or `tests/test_cli_init_config.py` changes were made.

## 2026-10-20 — inspect UTF-8 stdout correction

- Identified that hidden `codewhy inspect` used `typer.echo` with `ensure_ascii=False`, which can raise `UnicodeEncodeError` when Windows stdout uses a non-UTF-8 encoding such as cp1252.
- Added an inspect-only UTF-8 byte-stream writer in `src/codewhy/cli.py`; the existing `scan` output path remains unchanged. Deterministic JSON formatting, source exclusion, no writes, and no provider calls are preserved.
- Added a regression test using simulated cp1252 stdout and a Unicode arrow, without depending on the machine console encoding.
- Verification: `.venv\Scripts\python.exe -m pytest tests/test_inspect.py -q --basetemp .pytest-task03-stdout` — Passed: 6 tests.
- Verification: `.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-task03-full` — Passed: 221 tests; 3 skipped; 1 expected Windows symlink-privilege warning.

## 2026-10-20 — inspect skipped-structure crash correction

- Confirmed that `inspect_project()` indexed graph nodes for skipped extraction structures, although `build_graph()` correctly omits non-`ok` structures. This caused a `KeyError` and prevented JSON output.
- Updated inspect serialization to use empty relationship metadata when a structure is absent from the graph. Skipped status and warning fields remain serialized; graph semantics and virtual-environment ignore patterns were not changed.
- Added focused coverage for a skipped structure without a graph node and an oversized discovery skip.
- Verification: `.venv\Scripts\python.exe -m pytest tests/test_inspect.py -q --basetemp .pytest-task03-inspect-fix` — Passed: 8 tests.
- Verification: `.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-task03-inspect-full` — Passed: 223 tests; 3 skipped; 1 expected Windows symlink-privilege warning.
