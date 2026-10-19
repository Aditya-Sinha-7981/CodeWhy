# Review log — CW-02 extraction, integration with CW-01

Branch: `review/02-extraction`, created from `task/02-extraction` (Shregna, commit
`2c595d2`). Reviewer: Aditya (lead). The original author's log, `logs/02-extraction.md`,
is left unchanged.

## 2026-10-20

### Why this review exists

`task/02-extraction` was branched from the initial commit, before CW-01 existed.
The author therefore worked without `pyproject.toml`, `SourceFile`, or the shared
test helpers. They used a test-local `SourceFile` stand-in and a `requirements.txt`,
and said so in their log. As a result, every job in the branch's GitHub Actions
run (`39282302471`, 9 of 9) failed at `pip install -e ".[dev]"`: "neither
'setup.py' nor 'pyproject.toml' found". The tree-sitter wheel matrix has
therefore never actually run.

### Plan

1. Merge `main` (CW-01) into the branch. Resolve conflicts without changing
   behavior, as a separate merge commit.
2. Make the branch installable: tree-sitter dependencies into `pyproject.toml`,
   remove `requirements.txt`.
3. Replace the `SourceFile` stand-in with the real CW-01 type. Add tests that run
   real discovery output through extraction.
4. Fix extractor defects found by probing against task 02 §7, each with a
   regression test.
5. Fill the gaps against task 02 §6–§8: missing fixture cases, missing tests,
   `types` note in the architecture.

### Commit 1 — merge `main` into the branch

- One conflict: `tests/fixtures/builders.py` (add/add; both tasks created it).
  Resolved by keeping both `build_ignored_content` (CW-01) and
  `build_broken_files` (CW-02) exactly as written. No other file overlapped.
- After the merge: 147 tests passed (120 CW-01 + 27 CW-02).

### Commit 2 — review fixes

**Packaging / CI**
- `pyproject.toml`: added `tree-sitter>=0.23`, `tree-sitter-javascript>=0.23`,
  `tree-sitter-typescript>=0.23` (the only dependencies task 02 allows). The
  lower bound is tested, not guessed: the full suite and `check_wheels.py` pass
  with all three at `==0.23.0` on Python 3.11, and with the current 0.26.0 /
  0.25.0 / 0.23.2 on Python 3.14.
- Removed `requirements.txt` (unpinned, outside the task's allowed files, and
  ignored by the CI install step).
- A fresh venv with `pip install -e ".[dev]"` (exactly what the CI does), then
  `scripts/check_wheels.py`: OK.

**Extractor defects (all in `ts_treesitter.py` unless noted)**

| Defect | Example | Fix |
|---|---|---|
| `is_type_only` was decided by `text.startswith("import type")` | `import typeorm from "typeorm"` flagged type-only | Check the parse tree for the anonymous `type` token on the `import_statement` |
| `abstract class` not extracted | `export abstract class Shape {}` missing from `classes` and units | Treat `abstract_class_declaration` as a class |
| Anonymous default class not extracted (task §7 requires exported/default classes) | `export default class { run() {} }` missing | Treat the `class` node under `export default` as a class named `default` (same convention as anonymous default functions) |
| `is_async` was a regex for `async` in the declaration's text | `const f = () => async () => 1` flagged async | Check the function node's own `async` token (declaration, arrow/function value, or method) |
| Python syntax warning repeated the location (`python_ast.py`) | `SyntaxError: invalid syntax (x.py, line 1) (line 1)` | Use `SyntaxError.msg`, giving `SyntaxError: invalid syntax (line 1)` as task §7 specifies |

As a check, the 5 new regression tests were run against the original
extractor files; all 5 fail there and pass with the fixes.

**Fixtures**
- `build_broken_files`: the default `max_file_bytes=32` made its own
  `healthy.js` (35 bytes) too large, so only one of the "two healthy files"
  survived. The default is now 64; every file except `oversized.py` fits. Added
  path constants (`BROKEN_FILES_*`) so tests don't repeat literals. The builder
  was previously unused by any test.
- `python_js_mixed/frontend/App.jsx` (new) adds the cases task §7 Req. 6 lists
  that were missing: a directory import (`./components`), a `.js` specifier for a
  `.ts` file (`./utils.js` → `utils.ts`), an alias import (`@/api/client`), and a
  `.jsx` file. Existing fixture files are unchanged. Task 03 depends on these
  cases.

**Tests**
- `test_extraction_dispatch.py`: uses the real `codewhy.ignore.SourceFile`
  instead of the local stand-in. New: dispatch by every supported extension (via
  real discovery); `broken_files` through real discovery (discovery skips
  undecodable/oversized, extraction skips the syntax error, the empty
  `__init__.py` has no units, the internal-error path is never hit); determinism
  of both committed fixtures through discovery; `python_js_mixed` covers every
  required import form.
- `test_python_ast.py`: imports inside `if TYPE_CHECKING`, `try/except
  ImportError`, and a function body (required by task §8, previously untested);
  syntax-error warning format.
- `test_ts_treesitter.py`: `import type` vs `import typeorm`; abstract and
  anonymous default classes; `async` detection; multi-line signature (task §10
  risk).

**Architecture**
- `docs/ARCHITECTURE.md` → *Layer 1*: added `types` to the `FileStructure`
  field list, with one sentence on what it holds. Task 02 §2 requires this
  edit; no other contract changed.

### Commands run

- `.venv/bin/python -m pytest -q`: **166 passed** (Python 3.14.7, tree-sitter
  0.26.0 / 0.25.0 / 0.23.2).
- Python 3.11 + tree-sitter packages all `==0.23.0` (scratch venv via `uv`):
  **166 passed**; `check_wheels.py` OK.
- Fresh venv, `pip install -e ".[dev]"`, `check_wheels.py`, CI's pytest
  selection: OK, 27 passed.
- Real project: discovery + `extract_all` on this repository: 34 files, 9
  modules, 0 discovery skips, 34 `ok`. Spot-checked `src/codewhy/config.py`:
  functions, classes, imports, and units match the source.

### Checks still open (Passed / Failed / Not run)

- GitHub Actions wheel matrix (3 OS × Python 3.11–3.13): **Not run.** It runs on
  the first push of this branch. If any combination fails, task 02 says to stop
  and decide the supported range before merging.
- Extraction on a real JS/TS project: **Not run** (none available in this session).
  The author or lead should run discovery → `extract_all` on one and log any false
  skips.
- Windows: **Not run** locally. The author's log shows they developed on Windows;
  the CI matrix covers it.

### Left as-is (noted for the author, not defects against the spec)

- `import { type B, C }` records the binding as `"type B"` (as written). Task 03
  resolves by module, not names, so this is harmless. Revisit if a later task
  renders binding names.
- Abstract method signatures (`abstract area(): number;`) have no body and are not
  listed as methods; the spec only asks for `method_definition`.
- `declare function` ambient declarations are not extracted (not in the spec).
- The wheel workflow runs on every push to every branch, as the task specifies.
