# Review log — CW-03 resolution, graph, entry point, inspect

Branch: `review/03-resolution-graph`, created from `task/03-resolution-graph`
(Shregna, commit `38a4778`, already based on `main` with CW-01 and CW-02 merged).
Reviewer: Aditya (lead). The author's log, `logs/03-resolution-graph-entrypoint.md`,
is left unchanged.

## 2026-10-20

### Review findings (before any change)

Checked against `docs/ARCHITECTURE.md` (Layer 2, Entry Point Detection, CLI
surface), `docs/TESTS.md` (Layer 2), and `tasks/03-resolution-graph-entrypoint.md`.

- Baseline: `pytest -q` **226 passed** (Python 3.14.7). Python 3.11.11 with
  `networkx==3.0` (the lower bound): **226 passed**. GitHub Actions run
  `39383599007` on the task branch: success.
- Scope: every changed file is on the task's allowed list; only `networkx` added.
- `inspect tests/fixtures/nested_modules`: all 16 edges checked by hand against
  the fixture imports. Entry point, externals, and unresolved imports correct.
  Output is byte-identical across runs.

Defects found:

1. `scan` crashed with a traceback when `project.entry_point` names a missing
   file. `build_project` now raises `ConfigError`, but `scan` caught only
   `DiscoveryError`. (`inspect` handled it.)
2. Malformed root metadata crashed both `scan` and `inspect`. The architecture
   says to warn and treat it as absent. Reproduced: a non-UTF-8 `pyproject.toml`
   (`UnicodeDecodeError`), and valid TOML with the wrong shape, e.g.
   `project = "x"` (`AttributeError`).
3. `scan` did not report the edge count (task §7 Req. 5, §8). The author
   deliberately skipped this to avoid editing the CW-01 test that pins the
   exact `scan` text.
4. Manual check on two real projects: not done (an acceptance criterion).
5. A JS directory import that resolves to the project root (`"."` or `"./"` from a
   root file, `".."` from a subfolder, `"/"`) never found the root `index.*`:
   `normpath` spells the root `"."`, so candidates became `"..ts"` / `"./index.ts"`.
6. Found during the fix: absolute `from utils import *` (and `from pkg import *`)
   resolved to **external**. `*` is skipped as a name, so no target was found
   and the search fell through every root. This is a common case in flat-script
   projects.
7. Spec gap: relative `from .pkg import mod` resolved to `pkg/__init__.py`, never
   `pkg/mod.py`, although absolute `from pkg import mod` prefers the submodule.
8. Explicit `project.entry_point` naming a file that *is* in the project but was
   skipped (syntax error, too large) reported "does not exist".
9. Fixture gaps (task §7 Req. 6 and §10): the fixture had no third-party Python
   import, no namespace package (folder without `__init__.py`), and no absolute
   import resolved through the importer's directory.
10. Test gap: conventional entry-point names were each tested alone, so
    precedence among them was never checked.
11. Author's log: entries out of order, references to `requirements.txt` and a
    "blocked" networkx that were stale by the end, no "Hardware: N/A" line.
    Left unchanged; recorded here instead.

### Plan

1. `cli.py`: `scan` catches `ConfigError`; the summary line gains the edge count.
2. `entrypoint.py`: one reader for root metadata (UTF-8 with optional BOM; I/O
   and decode errors → warning, absent); shape-safe table lookup; an explicit
   entry point that was skipped gets a specific error.
3. `js_resolver.py`: treat a normalized `"."` base as the project root.
4. `python_resolver.py`: `*` targets the module file; relative `from .a import b`
   prefers the submodule per name, like absolute imports.
5. `docs/ARCHITECTURE.md`: record rule 4's relative-import half.
6. Fixture additions, regression tests, real-project check, this log.

### Changes

**Code**
- `src/codewhy/cli.py` (`scan`): catches `ConfigError` (`error: …`, exit 1).
  The summary is now `Found N source files in M modules with E import edges
  (K skipped, L other files). …`.
- `src/codewhy/entrypoint.py`:
  - `_read_metadata`: reads bytes and decodes as `utf-8-sig`. `OSError` and
    `UnicodeDecodeError` log a warning naming the file and return `None`.
  - `_table(data, *keys)`: returns `{}` when any level is missing or not a table.
    Used for `[project.scripts]` and `[tool.poetry.scripts]`.
  - `json.loads` also catches `RecursionError` (deeply nested input).
  - Explicit entry point: if the path is in `project.skips` or has a non-`ok`
    structure, the error reads `project.entry_point '<x>' is in the scanned
    project but was skipped (<reason>)`. A truly missing path still gets the
    exact message from task §7 Req. 4. Both remain fatal `ConfigError`s.
- `src/codewhy/graph/js_resolver.py`: a base of `"."` after `normpath` becomes
  `""`, so the root's `index.*` resolves.
- `src/codewhy/graph/python_resolver.py`:
  - Absolute: when the only imported name is `*`, the target is the module file
    (`from utils import *` → `utils.py`).
  - Relative `from .a import b, c`: per name, try `<base>/a/b.py` /
    `<base>/a/b/__init__.py`, else the file for `a`. Targets are the sorted
    union; `*` targets the file for `a`. This also makes namespace packages
    work (`from .plugins import loader` with no `plugins/__init__.py`).
  - Replaced a conditional expression used as a statement with an `if`, and
    removed a stray trailing comma.

**Architecture (contract change, lead decision)**
- `docs/ARCHITECTURE.md` → *Python resolution*, Relative: `from .a import b`
  now prefers the submodule `<base>/a/b.py` per imported name before
  `<base>/a.py` / `<base>/a/__init__.py`, matching the absolute rule. Targets are
  the sorted union; `*` targets the module file. Task 03 §7 Req. 1 still states
  the old relative wording; the architecture wins (CLAUDE.md order of authority).
  The task file is not edited here.

**Out-of-scope file (approved by the lead)**
- `tests/test_cli_init_config.py`: the two expected `scan` strings now include
  `with N import edges`. No other change to the file.

**Fixture `nested_modules/`**
- New namespace package `src/plugins/` (no `__init__.py`):
  - `loader.py`: `import requests` (third-party) and `from app.core import app`;
  - `registry.py`: `import loader` (absolute import resolved through the importer's
    own directory, root `D`).
- `src/app/worker.py`: adds `from plugins import registry` (namespace package
  via `R/src`) and `from .services import service` (resolves to
  `services/service.py` under the new rule).
- Edges 16 → 20; all 20 checked by hand. The full edge list, top-5, and the
  external union (`os`, `react`, `requests`) are asserted in `test_graph.py`.

**Tests** (24 new, plus 1 updated expectation)
- `test_python_resolver.py`:
  - relative submodule preference per name: submodule, package fallback, union;
  - relative namespace package, including an unresolved missing name;
  - star imports, absolute and relative;
  - absolute namespace package.
- `test_js_resolver.py`: root directory imports via `.`, `./`, `..`, `../` and `/`.
- `test_entrypoint.py`:
  - non-UTF-8 `pyproject.toml` / `package.json`: warning, then fall through;
  - 5 wrongly shaped TOML cases and 5 wrongly shaped JSON cases;
  - BOM-prefixed `package.json`;
  - an explicit entry point skipped by extraction, and one skipped by discovery;
  - conventional-name precedence, removing the winner each time.
- `test_inspect.py`: `scan` reports the edge count on the fixture; `scan` with a
  missing explicit entry point gives exactly one `error:` line and exit 1; `scan`
  and `inspect` both survive malformed metadata.
- `test_graph.py`: the nested edge list is extended, with top-5 and externals
  asserted.
- Check: with the original four source files restored, **18** of the new or
  updated tests fail. All pass with the fixes.

### Commands run

- `.venv/bin/python -m pytest -q`: **250 passed** (Python 3.14.7, networkx
  current, tree-sitter 0.26.0 / 0.25.0 / 0.23.2).
- Python 3.11.11, `networkx==3.0` (scratch venv via `uv`): **250 passed**.
- `.venv/bin/python -m pytest -q -m live_llm`: 250 deselected (no live tests).
- `git diff --check`: clean.
- `.venv/bin/python -m codewhy inspect tests/fixtures/nested_modules | head -60`:
  stable JSON. Entry point `src/app/cli.py`; 20 edges as listed in the test.
- `codewhy --help`: `inspect` not listed.

### Manual verification (task §8)

Ran `inspect` and `scan` (read-only) on three of the lead's own projects, plus
this repository. Only counts and resolution patterns are recorded here, no code.

| Project | Shape | Files / edges | Entry point |
|---|---|---|---|
| Flat-script Python tool | 9 `.py`, flat with a `test/` folder | 9 / 6 | `main.py` (conventional) |
| Mixed app | `backend/` Python (FastAPI) + `frontend/` React (`.jsx`) | 67 / 34 | `None` |
| TS service | `backend/src` TypeScript, ESM `.js` specifiers | 31 / 65 | `None` |
| This repo | `src/` layout | 61 / 99 | `src/codewhy/cli.py` (`[project.scripts]`) |

- **Edges checked by hand: Passed.** I compared 13 files' import lines with
  `imports_resolved` and `external_imports`:
  - 3 flat-script files: `import constants` / `import functions` from root and
    from `test/` (resolved through `R`);
  - 6 mixed-app files: `from api.chat import …` from `backend/main.py`, the
    package `__init__` relative imports, and React relative imports, including a
    `./index.css` that is correctly unresolved;
  - 4 TS files: `./config.js` → `config.ts`, and `../src/…` from `tests/`.

  Every resolved edge was correct, and every third-party or stdlib name was
  correctly external.
- **Most depended-on: Passed** for the flat-script tool (`constants.py`,
  `functions.py`), the TS service (`config.ts` 13, `logger.ts` 7,
  `models/recording.ts` 7, `services/jobs.ts` 7), and this repo
  (`extraction/model.py`, `config.py`). The mixed app's top-5 is skewed by gap A
  below.
- **Resolver gaps found (open, for the lead):**
  - **A. Python roots below the scanned folder.** In the mixed app, the backend
    runs from `backend/`, so its files write `from agent.memory import …`,
    `from config import settings`, `from providers.llm import …`. From
    `backend/agent/router.py` the search roots are `backend/agent`, `R`, `R/src`,
    none of which contains `agent/`. So `agent`, `config`, `providers` and `rag`
    land in `external_imports`: 35 file-to-package references, so at least 35 real edges are missing. This
    matches the specified heuristic (task §10 Known Risks), so it is not fixed
    here. It is common in `backend/` + `frontend/` repositories. A possible v1.x
    rule is to also search each ancestor of `D` up to `R`. That would be an
    architecture change.
  - **B. Entry point in a subfolder.** Both the mixed app (`backend/main.py`)
    and the TS service (`backend/package.json`, `backend/src/index.ts`) report
    `None`. Metadata and conventional names are read at the scanned root only,
    as specified; noted for README wording.
  - **C. Type-only imports are edges.** In the TS service, `import type { Job }`
    creates a `models/recording.ts ↔ services/jobs.ts` cycle. The spec does not
    exclude type-only imports, and cycles are allowed. Noted only.
- Windows: **Not run** locally (macOS only). The author's log shows the suite
  passing on Windows before this review. The wheel workflow covers Windows for
  extraction only.

### Hardware / model-dependent checks

N/A (no LLM, network, or hardware in this task).

### Left as-is (not defects against the spec)

- `@/utils` is reported as external package `@` (the first path segment). Task
  §7 Req. 2 only requires it to be external.
- Resolvers do not log decisions at debug level. The task allows debug logging
  only; it does not require it.
- `build_project` builds `ProjectModel` twice (once without the entry point,
  so it can be passed to `detect_entry_point`). Correct and cheap; not changed.

### Handoff for later tasks

- Task 05/06: `scan`'s stub line now includes the edge count. Task 08 replaces it.
- Task 07: the relative-import rule change alters edges (and therefore canonical
  edge lists) only for `from .pkg import mod` where `pkg/mod.py` exists, and for
  star imports. There is no cache yet, so nothing needs migrating.
- Gap A should be decided before the README is written (task 09), since it
  affects the most common monorepo layout.
