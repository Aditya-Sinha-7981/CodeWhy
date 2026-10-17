# codewhy Task Specification

> **This file is a work order for the assigned developer and their AI coding agent.**
>
> This task defines the scope of the assigned work. Do not expand the scope without explicit approval from the project lead.

---

## Task Information

**Task:** `Import resolution, dependency graph, entry-point detection, inspect`

**Task ID:** `CW-03` (index: `tasks/ALL-TASKS.md`)

**Depends on:** CW-01, CW-02

**Branch:** `task/03-resolution-graph` (suggested; create only when the user wants a branch)

**Priority:** `Critical`

**Status:** `Not Started`

**Work log:** `logs/03-resolution-graph-entrypoint.md`

---

# 1. Goal

## Objective

Connect the files. Deliverables:

1. **Python and JS/TS import resolvers** implementing the architecture's rules exactly, including the flat-script case (`import utils` in `main.py`) that is the product's motivating use case.
2. **The dependency graph** (`networkx.DiGraph`) with external/unresolved import metadata and canonical edge lists.
3. **Derived deterministic data:** `Depends On`, `Used By`, top-5 most depended-on files, module-level in-degree.
4. **Entry-point detection** with the fixed priority order.
5. **`ProjectModel` extended** with graph and entry point.
6. **`codewhy inspect [PATH]`**: the Phase-1 demo, a deterministic JSON dump of everything the tool knows, with no LLM.
7. The `nested_modules/` fixture.

## Why This Exists

The graph is what separates this tool from "summarize each file": it feeds "Used By", the importer/imported-by context in file prompts, module cohesion text, ordering under truncation, and cache keys. A resolver that only understands relative imports yields **zero edges** on exactly the projects the tool is built for. `inspect` makes every later layer debuggable without spending a token.

---

# 2. Authoritative Documentation

* `CLAUDE.md`
* `docs/ARCHITECTURE.md` — *Layer 2 — Import resolution and dependency graph* (Python resolution, JS/TS resolution, canonical edge lists, derived data), *Entry Point Detection*, *Module Definition*, *Unsupported Files*, *CLI surface* (`inspect`)
* `docs/TESTS.md` — *Layer 2 — Resolution and graph*, *Entry-point detection*, *Fixture projects*

---

# 3. Scope

## In Scope

* `src/codewhy/graph/{__init__,python_resolver,js_resolver,builder}.py`, `src/codewhy/entrypoint.py`.
* Extending `project.py` (`ProjectModel` gains `structures`, `graph`, `entry_point`) and `cli.py` (`inspect`).
* Runtime dep: `networkx`.
* Fixture `tests/fixtures/nested_modules/` (committed).
* Tests in §8.

## Out of Scope

* `tsconfig.json` `paths`/`baseUrl` alias resolution (alias imports are external in v1).
* Distinguishing stdlib from third-party packages.
* Resolving dynamic Python imports (`importlib`, `__import__`).
* Any LLM/provider code; rendering; cache.
* PageRank or clustering.

Do not implement "while we're here" improvements outside this scope.

---

# 4. Allowed Files / Areas

```text
pyproject.toml                               (add networkx only)
src/codewhy/graph/__init__.py
src/codewhy/graph/python_resolver.py
src/codewhy/graph/js_resolver.py
src/codewhy/graph/builder.py
src/codewhy/entrypoint.py
src/codewhy/project.py                       (extend)
src/codewhy/cli.py                           (add hidden `inspect`)
tests/fixtures/nested_modules/               (committed)
tests/test_python_resolver.py
tests/test_js_resolver.py
tests/test_graph.py
tests/test_entrypoint.py
tests/test_inspect.py
logs/03-resolution-graph-entrypoint.md
```

### May Modify If Required

```text
docs/ARCHITECTURE.md   (only for a contract change you are forced to make — logged)
```

---

# 5. Dependencies

| Dependency | Type | Notes |
|---|---|---|
| CW-01 `ProjectModel`, CW-02 `FileStructure` | Hard | |
| `networkx` | Runtime | Locked in the architecture. |

### Dependency Status

* [x] Dependency exists and is ready
* [ ] Dependency is being developed separately
* [ ] Dependency requires coordination
* [x] No external dependency beyond `networkx`

---

# 6. Contracts That Must Be Preserved

### Architecture

* Edge `A → B` means A imports B. One node per supported, successfully extracted file (`status == "ok"`). **No edges between Python and JS/TS files.** Cycles allowed; nothing may assume a DAG.
* Unresolved non-relative imports → node metadata `external_imports` (top-level package name only, sorted, unique). Unresolved **relative** imports → `unresolved_imports` (raw specifier). Neither ever becomes an edge.
* Canonical edge representation: `<source_path> -> <destination_path>`, `/` separators, sorted lexicographically, joined with `\n`. A module's relevant edges are all edges with **either** endpoint in the module; the project edge list is every edge.
* Entry-point priority exactly as specified; explicit `project.entry_point` that does not exist is a **fatal config error**; `pyproject.toml` and `package.json` are read for entry-point detection only.
* `inspect` is hidden from `--help`, makes no provider call, writes nothing, and its output contains no `source_text`.

### Data conventions

* All paths POSIX-relative. All lists sorted. JSON output is canonical (sorted keys, fixed indent of 2, trailing newline).

---

# 7. Implementation Requirements

### Requirement 1 — Python resolver (`graph/python_resolver.py`)

`resolve_python_import(importer_path, imp: ImportInfo, project_paths: frozenset[str]) -> Resolution` where `Resolution` is `Resolved(targets: tuple[str, ...])` or `Unresolved(kind: "external"|"relative", name: str)`. Work only against `project_paths` (the set of Python files in the project); no filesystem access.

* **Relative** (`level >= 1`): base dir = importer's directory, going up `level - 1` directories (stop at the project root; going above it → unresolved relative). `from .a import b` → try `<base>/a.py`, then `<base>/a/__init__.py`. `from . import x, y` → per imported name try `<base>/x.py`, `<base>/x/__init__.py`, else fall back to `<base>/__init__.py`; targets are the union, sorted. Nothing found → `Unresolved(relative, <as written, e.g. "..pkg.mod">)`.
* **Absolute:** search roots in order `<importer dir>`, `<project root>`, `<project root>/src`. In each root map the dotted name `a.b.c` to the **longest prefix** that is a project file: `a/b/c.py` or `a/b/c/__init__.py`, then `a/b.py` or `a/b/__init__.py`, then `a.py` or `a/__init__.py`. The first root yielding any match wins (do not merge across roots). For `from a.b import c, d`: for each imported name prefer submodule `a/b/c.py` / `a/b/c/__init__.py`; otherwise use the file for `a.b` itself; the result is the sorted union. For `import a.b.c` the target is the longest-prefix file.
* `import os`, `import numpy`, `import a` (no `a.py`) → `Unresolved(external, <top-level name>)`.
* Self-imports are dropped by the builder, not the resolver. Implicit package `__init__.py` executions (parents of the imported module) create **no** edges.

### Requirement 2 — JS/TS resolver (`graph/js_resolver.py`)

`resolve_js_import(importer_path, imp: ImportInfo, project_paths: frozenset[str]) -> Resolution`.

* Only specifiers starting with `.` or `/` are candidates (a leading `/` is project-root-relative inside the scanned folder, not the filesystem root). Everything else, including `@/utils`, `~/x`, `react`, `@scope/pkg`, is `Unresolved(external, <package name>)` where the package name for scoped packages is `@scope/pkg`, otherwise the first path segment.
* For a relative specifier from the importer's directory (normalize `..`/`.` segments; leaving the project root → unresolved relative):
  1. exact path if it already ends in a supported extension and exists;
  2. specifier + each extension in the order `.ts .tsx .js .jsx .mjs .cjs .mts .cts`;
  3. specifier as a directory: `<spec>/index` + each extension, same order;
  4. TS ESM convention: if the specifier ends in `.js`/`.jsx`/`.mjs`/`.cjs` and no such file exists, try `.ts`/`.tsx`/`.mts`/`.cts` with the same stem.
  Not found (including relative imports of `.css`, `.json`, images) → `Unresolved(relative, <raw specifier>)`.
* Candidate files are drawn only from JS/TS paths in `project_paths`.

### Requirement 3 — Graph builder (`graph/builder.py`)

* `build_graph(structures: Mapping[str, FileStructure]) -> nx.DiGraph`: nodes for `status == "ok"` files with attributes `language`, `external_imports` (sorted unique list), `unresolved_imports` (sorted unique list). Use the resolver matching each file's language with that language's file set only. Drop self-edges; dedupe edges.
* Derived helpers (pure, take the graph): `imports_of(path)`, `imported_by(path)` (sorted lists); `canonical_edges(graph, *, module_files: frozenset[str] | None = None) -> str` per the canonical format (module subset = edges with either endpoint in the set; `None` = all); `most_depended_on(graph, n=5)` (in-degree desc, ties by path, in-degree 0 excluded); `module_in_degree(graph, module_files)` = number of edges from files **outside** the module into it (used by task 05 for truncation ordering); `external_dependencies(graph, module_files)` = sorted union of `external_imports`.
* Skipped (`status == "skipped"`) files are not nodes; imports pointing at them count as unresolved relative/external as the resolver decides (they are not in `project_paths`).

### Requirement 4 — Entry point (`entrypoint.py`)

`detect_entry_point(project: ProjectModel, config: Config) -> str | None`, exactly per the architecture's priority list:

1. `config.project.entry_point`: must be in the project's supported files, else raise `ConfigError("project.entry_point '<x>' does not exist in the scanned project")`.
2. Package metadata at the **scanned root only**:
   * `pyproject.toml` via `tomllib`: entries of `[project.scripts]` then `[tool.poetry.scripts]` in file order; value `pkg.mod:func` → take `pkg.mod` through the Python resolver (as an absolute import from the root); first resolvable wins. Invalid TOML → log a warning, treat as absent.
   * `package.json` (JSON): `"main"` resolved relative to the folder containing `package.json` with the JS rules (accept only if it resolves to a project file; e.g. `dist/index.js` that is ignored or absent falls through); then `scripts.start` **only** if it matches `^(node|ts-node|tsx|nodemon)\s+(?:-\S+\s+)*(\S+)\s*$`, resolving the captured path like `main`. Anything else is not matched. Invalid JSON → warning, treat as absent.
3. Conventional filenames in exact order: `main.py`, `__main__.py`, `app.py`, `index.js`, `index.ts`, `src/main.py`, `src/index.js`, `src/index.ts`.
4. Otherwise `None`.

### Requirement 5 — Project model and `inspect`

* `ProjectModel` gains `structures: dict[str, FileStructure]`, `graph: nx.DiGraph`, `entry_point: str | None`. `build_project` now runs extraction, graph build, and entry-point detection. Keep it cheap and deterministic; `build_project` remains free of any network.
* `codewhy inspect [PATH]` (hidden): prints canonical JSON to stdout with keys `root_name`, `entry_point`, `modules` (path, files), `files` (each `FileStructure.to_dict(include_source=False)` plus `imports_resolved` (sorted targets), `imported_by`, `external_imports`, `unresolved_imports`), `edges` (sorted `[source, destination]` pairs), `most_depended_on`, `skips`, `other_files`. A `ConfigError` → stderr + exit 1.
* The `scan` stub from task 01 now reports modules/edges counts as well.

### Requirement 6 — Fixture `nested_modules/`

15+ realistic files, 3+ levels deep: a `src/` layout package with sub-packages, `__init__.py` files (some re-exporting), relative imports at levels 1 and 2, a module imported by many files, a deliberate cycle between two modules, a folder containing only subfolders, an absolute import of a sibling module, third-party and stdlib imports, plus one JS subfolder. Include a small `pyproject.toml` with `[project.scripts]` pointing into the package so entry-point detection has a real case.

### Error Handling

* Resolvers never raise on odd input (empty module names, `from . import *`, absurd `..` depth); they return `Unresolved`.
* A project with zero edges is valid.
* Log resolver decisions at debug level only; never log source text.

---

# 8. Testing Requirements

## Automated Tests

* `test_python_resolver.py` (each case a `make_project`-style path set): flat script `import utils` → edge (**write this test first; it is the acceptance test for the whole task**); `from solver import solve`; relative levels 1 and 2; `from . import x` submodule vs `__init__` fallback; `from a.b import c` prefers `a/b/c.py` over `a/b/__init__.py`; `src/` layout root; longest-prefix (`import a.b.c` with only `a/b.py`); root-order precedence (local `D/utils.py` beats `R/utils.py`); first root wins, no merging; `os`/`numpy`/`a` unresolved external with correct top-level names; unresolved relative recorded; relative import above project root → unresolved; no implicit parent-`__init__` edges.
* `test_js_resolver.py`: `./foo` prefers `foo.ts` over `foo.js`; directory `index.*`; exact extension; `.js` specifier resolving to `.ts`; `/rooted` specifier; bare, scoped (`@scope/pkg/sub` → `@scope/pkg`) and alias (`@/utils`) externals; `./styles.css` / `./data.json` unresolved relative; `..` leaving the root.
* `test_graph.py`: `python_js_mixed` → **zero** edges between Python and JS file sets; cycle builds and queries work; canonical edge list: `/` separators, sorted, insertion-order independent, module-subset semantics (either endpoint); `imports_of`/`imported_by`/`most_depended_on` ordering and exclusion of in-degree 0; `module_in_degree`; `external_dependencies` union; skipped files are not nodes; `nested_modules` expected edge list asserted in full.
* `test_entrypoint.py` (parametrized over `make_project` trees): explicit wins; nonexistent explicit → `ConfigError`; `[project.scripts]` resolves `pkg.mod:func`; first resolvable entry wins and unresolvable ones fall through; `[tool.poetry.scripts]` after `[project.scripts]`; `package.json` `main`; `main` pointing at an ignored/missing file falls through; `scripts.start` accepted for `node|ts-node|tsx|nodemon [flags] <path>`, not for `react-scripts start` or `npm run x`; conventional names in exact order; nothing → `None`; invalid TOML/JSON → warning and fall through.
* `test_inspect.py`: JSON snapshot on `nested_modules` (deterministic across two runs and across file-creation order); no `source_text`; no provider/network (monkeypatch socket creation to fail the test if touched); writes nothing (tree snapshot); hidden from `--help`; `scan` stub reports edge counts.

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m codewhy inspect tests/fixtures/nested_modules | head -60
```

Expected result:

```text
all tests pass offline; inspect output is stable JSON with the expected edges and entry point
```

## Hardware / Model-Dependent Checks

None. Record "N/A" in the work log.

## Manual Verification

1. Run `codewhy inspect` on two of your own real projects (include one with flat scripts and absolute imports). Verify the edge list by hand against the actual imports for at least 10 files.
2. Check the "Most depended-on" list matches your intuition about the project's core files.
3. Look for imports that *should* have resolved but landed in `external_imports`/`unresolved_imports`; log each (they reveal resolver gaps).

---

# 9. Acceptance Criteria

* [ ] The flat-script case resolves (`main.py` → `utils.py`, `solver.py`).
* [ ] Every resolution rule in §7 is implemented and tested; no cross-language edges.
* [ ] Canonical edge lists are stable and match the architecture format.
* [ ] Entry-point detection follows the exact priority; explicit-missing is fatal.
* [ ] `inspect` is hidden, offline, read-only, deterministic, and source-free.
* [ ] Manual verification on two real projects recorded, including any resolver gaps found.
* [ ] No dependencies beyond `networkx`.
* [ ] `git diff` reviewed; work log updated.
* [ ] If a commit was authorized: staged diff reviewed, only intended files, no AI attribution, no push/PR.

---

# 10. Known Risks

* **Search-root order is a heuristic.** `D`, `R`, `R/src` fits scripts and common layouts but can mis-resolve when a local file shadows a package name; the behavior is deliberate and documented, not a bug.
* **`from . import x` ambiguity** (submodule vs attribute of `__init__`). Prefer the submodule; fall back to `__init__.py`; test both.
* **Alias imports silently become external** (`@/components/Button`). This is a documented v1 limitation; surface it in `inspect` so users and the README can explain it.
* **Namespace packages** (no `__init__.py`) resolve through plain file paths here; confirm the fixture covers one and the behavior is sensible.
* **Case-insensitive filesystems** can make `Utils` and `utils` collide; resolution compares exact strings from the discovered path set.
* **Large JSON from `inspect`** on big projects; that is fine for a developer tool, but do not pretty-print by default in a way that breaks determinism.

---

# 11. Open Questions

* **requires clarification:** Should `unresolved_imports` ever appear in the generated docs? — Recommended: no for v1 (visible via `inspect` only), as the architecture states.
* **requires clarification:** Treat `export * from './x'` as an edge? — Recommended: yes (it is an `export … from` source, already collected by task 02).

---

# 12. Handoff Notes

* Task 05 consumes `imports_of`/`imported_by` (file prompts), `canonical_edges`, `module_in_degree`, `external_dependencies`, `most_depended_on`, and `entry_point`.
* Task 06 renders `Depends On`, `Used By`, `Most depended-on files`, `Entry Point`.
* Task 07 hashes the canonical edge lists and the entry point into cache keys.
* Record in the log: any resolver gaps found on real projects, decisions on ambiguous imports, and architecture edits.

If the task is complete:

> No outstanding handoff notes.

---

# 13. Definition of Done

See `CLAUDE.md` → *Definition of done*. Do not commit, push, open a PR, or merge unless the user explicitly authorizes that specific action.

## Final Agent Instruction

Before making changes: read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, this file, and the logs of tasks 01–02; run `git status`; write your plan into `logs/03-resolution-graph-entrypoint.md`.

During implementation: write the flat-script resolution test first; stay in scope; do not invent architecture; keep determinism and POSIX-path conventions.

If the task requires something not covered here, stop and ask the project lead.

Before handing off: run the tests, run `inspect` on real projects, review `git diff` and `git status`, update the log, and report what changed, what was verified, and what is untested or unresolved.
