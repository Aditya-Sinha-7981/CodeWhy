# codewhy Task Specification

> **This file is a work order for the assigned developer and their AI coding agent.**
>
> This task defines the scope of the assigned work. Do not expand the scope without explicit approval from the project lead.

---

## Task Information

**Task:** `Extraction layer (Python ast + JS/TS tree-sitter) and wheel verification`

**Task ID:** `CW-02` (index: `tasks/ALL-TASKS.md`)

**Depends on:** CW-01

**Branch:** `task/02-extraction` (suggested; create only when the user wants a branch)

**Priority:** `Critical`

**Status:** `Not Started`

**Work log:** `logs/02-extraction.md`

---

# 1. Goal

## Objective

Turn each discovered `SourceFile` into a language-agnostic `FileStructure`, deterministically and without ever raising on bad input. Deliverables:

1. **`FileStructure` and friends** (`extraction/model.py`), serializable to canonical JSON.
2. **Python extractor** on stdlib `ast`.
3. **JS/TS extractor** on `tree-sitter` with prebuilt grammar wheels.
4. **`top_level_units`** with contiguous line coverage (chunking in task 05 depends on this).
5. **A dispatcher** `extract_file(SourceFile) -> FileStructure`.
6. **Early proof of the "one command, no compiler" claim:** a script and a CI workflow that install and exercise the tree-sitter wheels on macOS, Linux, and Windows across Python 3.11–3.13.
7. Fixtures `flat_scripts/`, `python_js_mixed/` and a `build_broken_files` builder.

No import resolution, no graph, no LLM.

## Why This Exists

Layer 1 is the foundation of the product claim "facts come from code." If extraction is wrong or non-deterministic, every summary and every cache key built on it is wrong. And if the tree-sitter wheels do not install cleanly on a user's OS, the whole zero-setup promise fails; that has to be learned in week one, not at release.

---

# 2. Authoritative Documentation

* `CLAUDE.md`
* `docs/ARCHITECTURE.md` — *Layer 1 — Extraction*, *Supported extensions*, *Failure boundaries*, *Unsupported Files*
* `docs/TESTS.md` — *Layer 1 — Extraction*, *Fixture projects*

Note the one extension to the architecture's `FileStructure`: a `types` list (TS interfaces, type aliases, enums) so TypeScript declarations show up in "Defined Here". Python leaves it empty. Add a line about it to `docs/ARCHITECTURE.md` → *Layer 1* in this task.

---

# 3. Scope

## In Scope

* `src/codewhy/extraction/{__init__,model,python_ast,ts_treesitter}.py`.
* Runtime deps: `tree-sitter`, `tree-sitter-javascript`, `tree-sitter-typescript`.
* `scripts/check_wheels.py` and `.github/workflows/wheels-check.yml`.
* Fixtures: committed `flat_scripts/`, `python_js_mixed/`; `build_broken_files` in `tests/fixtures/builders.py`.
* Tests in §8.

## Out of Scope

* Import **resolution** (mapping an import to a project file) — task 03. Extractors only record imports as written.
* Graph, entry point, `inspect`.
* Any LLM or provider code.
* Extracting Python module-level constants/variables, nested functions, or JS/TS object-literal methods. Only what §7 lists.
* Reading `tsconfig.json`.

Do not implement "while we're here" improvements outside this scope.

---

# 4. Allowed Files / Areas

```text
pyproject.toml                               (add the three tree-sitter dependencies only)
src/codewhy/extraction/__init__.py
src/codewhy/extraction/model.py
src/codewhy/extraction/python_ast.py
src/codewhy/extraction/ts_treesitter.py
scripts/check_wheels.py
.github/workflows/wheels-check.yml
tests/fixtures/flat_scripts/                 (committed)
tests/fixtures/python_js_mixed/              (committed)
tests/fixtures/builders.py                   (add build_broken_files)
tests/test_python_ast.py
tests/test_ts_treesitter.py
tests/test_extraction_dispatch.py
tests/test_extraction_units.py
logs/02-extraction.md
```

### May Modify If Required

```text
docs/ARCHITECTURE.md   (the `types` field note; any contract change you must make, logged)
```

---

# 5. Dependencies

| Dependency | Type | Notes |
|---|---|---|
| CW-01 (`SourceFile`, discovery, test helpers) | Hard | |
| `tree-sitter`, `tree-sitter-javascript`, `tree-sitter-typescript` | Runtime | Prebuilt wheels; the point of the verification. |
| A GitHub repository with Actions enabled | Environment | Needed to run the OS/Python matrix. If none exists yet, record **Not run (no remote)** — do not fake it. |

### Dependency Status

* [x] Dependency exists and is ready
* [ ] Dependency is being developed separately
* [x] Dependency requires coordination (GitHub remote for the matrix)
* [ ] No external dependency

---

# 6. Contracts That Must Be Preserved

### Architecture

* Layer 1 never calls an LLM and never raises on bad input: malformed source → `status="skipped"` with a warning.
* Same input → byte-identical serialized output.
* JS/TS grammar choice: `.ts/.mts/.cts` → TypeScript grammar; `.tsx` → TSX grammar; `.js/.jsx/.mjs/.cjs` → JavaScript grammar (it handles JSX).
* Imports collected from the **whole tree** (Python: inside functions, `try/except ImportError`, `if TYPE_CHECKING`). JS/TS: static `import … from`, `export … from`, `require('literal')`, dynamic `import('literal')`; non-literal arguments are ignored.
* Unit boundaries: units cover the file contiguously; non-definition statements attach to the **following** unit; trailing ones to the last unit.

### Data conventions

* Paths are project-relative POSIX strings; `line_count` and line numbers are 1-based and computed on the already-normalized (`\n`) text.

### Safety

* The extractor must not execute or import the code it parses. Python parsing uses `ast.parse` only.

---

# 7. Implementation Requirements

### Requirement 1 — Model (`extraction/model.py`)

Frozen dataclasses, all with `to_dict()` returning plain, key-sorted-on-serialize structures, and `to_canonical_json()` (sorted keys, `ensure_ascii=False`, fixed separators):

* `FunctionInfo(name, signature, docstring | None, start_line, end_line, is_async, decorators: list[str])`
* `ClassInfo(name, bases: list[str], docstring | None, methods: list[FunctionInfo], start_line, end_line, decorators: list[str])`
* `TypeInfo(name, kind: "interface"|"type"|"enum", start_line, end_line)` (TS only)
* `ImportInfo(kind: "python"|"js", module: str, names: list[str], level: int, line: int, is_type_only: bool)` — for Python `module` is the dotted module as written (`""` for `from . import x`) and `level` is the relative level; for JS `module` is the raw specifier and `level` is 0.
* `UnitInfo(name, kind: "function"|"class"|"type"|"module", start_line, end_line, children: list[UnitInfo])`
* `FileStructure(path, language, source_text, line_count, functions, classes, types, imports, top_level_units, status: "ok"|"skipped", warning | None)`

`to_dict(include_source: bool = True)`; `inspect` (task 03) will call it with `False`.

### Requirement 2 — Python extractor

* `ast.parse(source, filename=path)`. Catch `SyntaxError`, `ValueError` (e.g. null bytes), `RecursionError`, `MemoryError` → `skipped` with `warning="<ExceptionType>: <message> (line N)"`.
* `functions`: top-level `def`/`async def` only. `classes`: top-level classes with their direct methods (not nested classes, not nested functions).
* `signature`: `def name(<ast.unparse of args>) -> <ast.unparse of returns>` (omit `->` if none; `async def` prefix when async). Decorators listed as unparsed strings without the `@`.
* `docstring`: `ast.get_docstring(node, clean=True)`.
* `imports`: `ast.walk` over the whole tree; one `ImportInfo` per `import a.b as c` (module `a.b`, names `[]`) and per `from m import x, y` (module `m`, names `[x, y]`, `level` from the node); `from __future__` included as written; ordered by `(line, col)`.
* `line_count`: `len(text.splitlines())` (empty file → 0).
* **`top_level_units`:** iterate `tree.body`; each function/class is a unit named after the definition (start line includes decorators). A unit's range is `(previous_unit_end + 1) .. its own end`, so comments, blank lines, imports and other statements between definitions attach to the following unit; the last unit's end is extended to `line_count`. A file with no definitions gets one unit of kind `module` named after the file spanning `1..line_count`. An empty file has no units. Class units carry their methods as children, with the same contiguity rule inside the class.

### Requirement 3 — JS/TS extractor

* Use the **current** `tree-sitter` Python API for the installed version (`Language(...)`, `Parser(language)`); verify against the installed versions and record the API shape and versions in the work log. TS and TSX are separate languages from `tree_sitter_typescript`.
* If the root node has errors (`has_error`), return `skipped` with `warning="syntax errors (first at line N)"` (find the first error/missing node). If a grammar cannot be loaded, return `skipped` with `warning="tree-sitter grammar unavailable: <detail>"`. Accept that very new syntax may produce false skips and say so in the work log.
* `functions` (top level only): function and generator declarations; `const|let|var name = <arrow function | function expression>`; `export`-wrapped versions of all of these; `export default function`/arrow (named `default` if anonymous). `classes`: class declarations (also exported/default) with `method_definition` children (include `constructor`, getters/setters as written, `static`, `async`).
* `types` (TS/TSX): `interface_declaration`, `type_alias_declaration`, `enum_declaration`, exported or not.
* `signature`: source text from the declaration's start to the start of its body, whitespace collapsed to single spaces, `export `/`export default ` prefix **kept as written**; for arrow/function-expression consts include the `const name =` part. `docstring`: the contiguous JSDoc (`/** … */`) comment ending on the line directly before the declaration, with the `/**`, `*/` and leading `*` stripped and whitespace collapsed; `None` if absent.
* `imports`: `import_statement` (`names` = imported bindings as written, `import type` → `is_type_only`), `export_statement` with a `source`, `call_expression` where the callee is the identifier `require` and the sole argument is a plain string literal, `call_expression` where the callee is `import` and the argument is a plain string literal. Dedupe exact duplicates (same module, names, kind, line). Template literals and non-literals are ignored.
* `top_level_units`: same contiguity rule as Python over top-level statements of the program; definition-bearing statements (function, class, arrow-const, interface/type/enum, and their `export` wrappers) are units; other statements attach to the following unit. Class units carry method children.
* `line_count`: same definition as Python.

### Requirement 4 — Dispatcher

* `extract_file(source: SourceFile) -> FileStructure` chooses the extractor by language/extension. Wrap each extractor so **any** unexpected exception becomes `skipped` with `warning="internal extractor error: <ExceptionType>: <message>"` and a `logging` warning with the traceback at debug level. Tests must assert the expected cases never hit this path.
* `extract_all(files: Iterable[SourceFile]) -> dict[str, FileStructure]` keyed by path, sorted by path.

### Requirement 5 — Wheel verification

* `scripts/check_wheels.py`: imports `tree_sitter`, `tree_sitter_javascript`, `tree_sitter_typescript`; parses one tiny snippet per grammar (JS, JSX, TS, TSX); prints versions and `OK`; exits non-zero with a clear message on any failure. Must run with no network and no compiler.
* `.github/workflows/wheels-check.yml`: matrix `os: [ubuntu-latest, macos-latest, windows-latest]` × `python: ["3.11", "3.12", "3.13"]`; steps: checkout, setup-python, `pip install -e ".[dev]"`, `python scripts/check_wheels.py`, `pytest tests/test_python_ast.py tests/test_ts_treesitter.py -q`. Trigger on `push` and `workflow_dispatch`.
* Record in the work log: which combinations passed, which failed and how (e.g. no wheel for macOS arm64 on 3.13). **If any combination fails, stop and tell the project lead** — it changes the supported-version statement or the dependency choice.
* If there is no GitHub remote yet, record the check as **Not run (no remote)** and say what the user must do.

### Requirement 6 — Fixtures

* `tests/fixtures/flat_scripts/`: `main.py`, `solver.py`, `utils.py` — a small but real backtracking solver (e.g. N-Queens or subset-sum). `main.py` does `import utils` and `from solver import solve`; `solver.py` does `from utils import …`. Mix documented and undocumented functions; one class; one function inside a `try/except ImportError` import.
* `tests/fixtures/python_js_mixed/`: a Python side (`backend/app.py`, `backend/db.py`, imports between them) and a JS/TS side exercising: ESM `import`, CommonJS `require`, `export … from`, a dynamic `import('./lazy')`, `.tsx` with JSX, a directory import (`./components`), a `.js` specifier that points at a `.ts` file, an alias import (`@/utils`), a bare package import (`react`), and a `./styles.css` import.
* `build_broken_files(tmp_path)`: a Python file with a syntax error, an empty `__init__.py`, a file with invalid UTF-8 bytes, a supported file larger than a small `max_file_bytes` the test sets, a symlink loop (skip creation with a recorded reason where unsupported), and two healthy files.

### Error Handling

* Extractors never raise. Skips carry a human-readable warning that includes line information where available.
* A skipped file is data: it stays in the result with `status="skipped"`; later layers exclude it. Do not drop it silently.

---

# 8. Testing Requirements

## Automated Tests

* `test_python_ast.py`: functions, classes, methods, signatures (including defaults, `*args/**kwargs`, annotations, async), decorators, docstrings; relative vs absolute imports with `level`; imports inside functions, `try/except ImportError`, `if TYPE_CHECKING`; `from __future__`; nested functions/classes not listed; syntax error → skipped with line; null-byte source → skipped; empty file.
* `test_ts_treesitter.py`: JS and TS: declarations, arrow consts, exported/default forms, class methods, getters/setters/static, `types` for TS, JSDoc docstring capture, `.tsx`/`.jsx` parse; every import form collected; non-literal `require(x)`/`import(x)` ignored; `import type`; syntax-error file → skipped; grammar-unavailable path (simulate by monkeypatching the loader).
* `test_extraction_units.py`: **property test** over every fixture file and a set of synthetic files: units have no gaps or overlaps and span `1..line_count`; leading imports/statements attach to the following unit; trailing statements extend the last unit; class children are contiguous within the class; a definition-free file gets one `module` unit; empty file has none.
* `test_extraction_dispatch.py`: dispatch by extension; **determinism** (extract twice → identical `to_canonical_json`); `broken_files` produces exactly the expected skips with warnings and healthy files `ok`; internal-error wrapper converts a deliberately raising extractor into a `skipped` result; `extract_all` is sorted.
* Parsing happens on the interpreter's own grammar: add one test pinning behavior for a construct valid on newer Pythons only if it can be expressed portably; otherwise note the limitation in the log.

Run:

```bash
.venv/bin/python -m pytest tests/test_python_ast.py tests/test_ts_treesitter.py tests/test_extraction_units.py tests/test_extraction_dispatch.py -q
.venv/bin/python scripts/check_wheels.py
```

Expected result:

```text
all tests pass offline; check_wheels.py prints OK and versions
```

## Hardware / Model-Dependent Checks

Record each as Passed / Failed / Not run (reason) in `logs/02-extraction.md`:

* Wheel matrix on macOS, Linux, Windows × Python 3.11, 3.12, 3.13 (GitHub Actions).
* Extraction run on at least two of your own real projects (see Manual Verification).

## Manual Verification

1. Write a throwaway script (not committed) that runs discovery → `extract_all` on two real local projects of yours (one Python, one JS/TS if you have one) and prints skips and counts. Read a few `FileStructure` dumps against the source.
2. Check that every skipped file in those projects is genuinely unparseable or unsupported, not a false skip; log any false skips from the JS/TS grammar.
3. Confirm no code path imports or executes the target project's modules.

---

# 9. Acceptance Criteria

* [ ] Python and JS/TS extraction match §7 on the fixtures.
* [ ] Malformed, undecodable, oversized and grammar-missing inputs produce `skipped` results, never exceptions.
* [ ] Unit coverage property holds on every fixture file.
* [ ] Extraction is byte-identical across runs.
* [ ] `scripts/check_wheels.py` passes locally; the OS/Python matrix is recorded (or **Not run (no remote)**); failures were escalated.
* [ ] `docs/ARCHITECTURE.md` mentions the `types` field.
* [ ] No dependencies beyond the three tree-sitter packages.
* [ ] No `print` in library code; no code from scanned projects in logs.
* [ ] `git diff` reviewed; work log updated.
* [ ] If a commit was authorized: staged diff reviewed, only intended files, no AI attribution, no push/PR.

---

# 10. Known Risks

* **tree-sitter Python API churn.** Bindings changed notably between releases; write against the installed version, pin sensible lower bounds in `pyproject.toml`, and log them.
* **Wheel gaps.** A missing wheel for one OS/Python combination undermines the zero-install promise. Finding it here is the entire point of the matrix.
* **Parser-version dependence.** Python parsing uses the running interpreter, so code using syntax newer than the interpreter is skipped with a syntax error. This is documented behavior for the README (task 09), not a bug to work around.
* **JS/TS false skips.** Error-tolerant parsing means `has_error` can trip on valid but newer syntax. Log false skips you observe; do not weaken the rule here.
* **Signature fidelity.** Collapsing whitespace in TS signatures can hide formatting but must never change tokens; tests should include multi-line signatures.

---

# 11. Open Questions

* **requires clarification:** If a wheel is missing on some OS/Python pair, drop that version from the supported range or switch parsers? — Recommended: narrow the supported Python range in the README and `requires-python` rather than add a compiler requirement.
* **requires clarification:** Should skipped JS/TS files with only `has_error` still contribute their imports? — Recommended: no (keep Python and JS behavior identical); revisit only if false skips are common.

---

# 12. Handoff Notes

* Task 03 consumes `FileStructure.imports` (as written) and implements all resolution; it also uses `flat_scripts/` and `python_js_mixed/`.
* Task 05 consumes `top_level_units` for chunking and `source_text`/`line_count` for prompt assembly.
* Task 06 renders `functions`, `classes`, `types` in "Defined Here".
* Record in the log: tree-sitter versions and API shape, wheel-matrix results, observed false skips, and architecture edits.

If the task is complete:

> No outstanding handoff notes.

---

# 13. Definition of Done

See `CLAUDE.md` → *Definition of done*. Do not commit, push, open a PR, or merge unless the user explicitly authorizes that specific action.

## Final Agent Instruction

Before making changes: read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, this file, and `logs/01-scaffold-config-discovery.md`; run `git status`; write your plan into `logs/02-extraction.md`.

During implementation: stay in scope; do not invent architecture; keep determinism and POSIX-path conventions; never execute scanned code; test incrementally.

If the task requires something not covered here, stop and ask the project lead.

Before handing off: run the tests and the wheel script, review `git diff` and `git status`, update the log, and report what changed, what was verified, and what is untested or unresolved.
