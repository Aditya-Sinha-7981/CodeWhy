# codewhy Task Specification

> **This file is a work order for the assigned developer and their AI coding agent.**
>
> This task defines the scope of the assigned work. Do not expand the scope without explicit approval from the project lead.

---

## Task Information

**Task:** `Rendering (single-file, docs-tree, render levels) and real scan wiring`

**Task ID:** `CW-06` (index: `tasks/ALL-TASKS.md`)

**Depends on:** CW-01 through CW-05

**Branch:** `task/06-render-scan` (suggested; create only when the user wants a branch)

**Priority:** `Critical` (first point where the tool is usable end to end)

**Status:** `Not Started`

**Work log:** `logs/06-render-and-scan.md`

---

# 1. Goal

## Objective

Make `codewhy scan` real. Deliverables:

1. **Section builders** that combine deterministic data with LLM prose per the documentation contract.
2. **Single-file output** (`PROJECT_DOCS.md`): header block, fixed preamble, overview, modules, files; **render levels 0/1/2** chosen against `token_budget`.
3. **docs-tree output** (`codewhy-docs/`), plus `both`.
4. **Overwrite protection** and stale-doc cleanup.
5. **`scan` wired end to end:** config → provider → consent → preflight → discovery → extraction → graph → passes → render → summary line. (No cache yet, no `--dry-run`, minimal progress.)
6. **Golden-file tests** that freeze the exact heading layout.
7. The first **live E2E** on `flat_scripts`.

## Why This Exists

The output file is the entire product from a user's point of view. Its structure must be stable, safe (never destroys user files, never lets model text corrupt the document), and sized for pasting into a chat. After this task a real user can run the tool.

---

# 2. Authoritative Documentation

* `CLAUDE.md`
* `docs/ARCHITECTURE.md` — *Documentation contract*, *Output* (single file, preamble, render levels, docs-tree, timestamps and idempotence, overwrite protection), *CLI surface* (`scan` flags, exit codes, run summary), *Privacy and remote endpoints*, *Failure boundaries*, *Unsupported Files*
* `docs/TESTS.md` — *Rendering / output*, *CLI*, *E2E*

---

# 3. Scope

## In Scope

* `src/codewhy/render/{__init__,sections,single_file,docs_tree}.py`, an output-writing helper (atomic writes are task 08; here use a simple write-then-replace is fine), `cli.py` (`scan`).
* Tests in §8, golden files under `tests/golden/`.
* A live E2E test marked `live_llm` on `flat_scripts`.

## Out of Scope

* The cache, `--full` behavior (accept the flag; without a cache every run is already full), `--dry-run` (task 08).
* Rich progress UI beyond one plain line per item; richer progress is task 08.
* Interrupt handling, atomic-write guarantees, the failure-boundary matrix (task 08).
* Layer 5, new providers.

Do not implement "while we're here" improvements outside this scope.

---

# 4. Allowed Files / Areas

```text
src/codewhy/render/__init__.py
src/codewhy/render/sections.py
src/codewhy/render/single_file.py
src/codewhy/render/docs_tree.py
src/codewhy/cli.py                              (scan)
tests/golden/                                   (frozen expected outputs)
tests/test_render_sections.py
tests/test_render_single_file.py
tests/test_render_docs_tree.py
tests/test_scan_integration.py
tests/test_e2e_live.py                          (live_llm)
logs/06-render-and-scan.md
```

### May Modify If Required

```text
docs/ARCHITECTURE.md   (document the exact heading layout below if you change it — logged)
src/codewhy/summarize/*  (only to expose data the renderer needs, no behavior change)
```

---

# 5. Dependencies

| Dependency | Type | Notes |
|---|---|---|
| CW-05 `RunResult`/`PassResult`, CW-04 `build_provider`/`ensure_remote_ack`/`preflight`, CW-03 graph helpers | Hard | |
| Ollama or a Gemini key | Environment | Only for the live E2E. |

### Dependency Status

* [x] Dependency exists and is ready
* [ ] Dependency is being developed separately
* [x] Dependency requires coordination (live model)
* [ ] No external dependency

---

# 6. Contracts That Must Be Preserved

### Architecture

* LLM text is inserted as plain paragraphs; **line-leading `#` is escaped** so model output cannot create headings. Failed results render as the visible placeholder `[generation failed]` in the right section (never as an empty section, never as normal prose).
* Deterministic sections are rendered from current extraction/graph data: `Depends On`, `Used By`, `Defined Here`, `Files in this module`, `Internal dependencies`, `External dependencies`, `Entry Point`, `Modules`, `Most depended-on files`, `Other files in this project` (capped at 100 paths then `… and N more`).
* `Notes` omitted when null/empty. `Most depended-on files` omitted when none.
* Single-file order: header block → H1 title → preamble → project overview → modules (path order) → files (path order). One header block; no per-section frontmatter. Preamble text is **exactly** the architecture's paragraph.
* `generated_at` comes from the `PassResult.generated_at` behind the document (single-file header = the latest of all pass results), never from the wall clock at render time.
* Render levels (single-file only): choose the **lowest** level whose estimated size is ≤ `output.token_budget`; Level 2 over budget still emits and warns; docs-tree is always Level 0. Pure rendering, no extra LLM calls.
* Output location: `<root>/PROJECT_DOCS.md` (configurable name), docs-tree in `<root>/codewhy-docs/` (configurable dir, default **not** `docs/`); `both` writes both.
* Overwrite protection: an existing target not starting with codewhy frontmatter (`generated_by: codewhy`) → exit 1 unless `--force`. Stale docs in docs-tree are removed **only** if they carry codewhy frontmatter.
* Remote consent and preflight happen **before any extraction** (fail fast); a local Ollama never prompts.
* Exit codes: `0` clean; `1` fatal (config, provider unreachable/model missing, remote not acknowledged, refused overwrite, auth/bad-request abort); `2` completed but ≥1 skipped file (parse/size/decode) or ≥1 `[generation failed]`.

### Data conventions

* Paths POSIX-relative; sorted everywhere; output uses `\n` newlines and ends with one trailing newline.

### Safety

* Never write outside the scanned folder. Never modify source files. Never follow symlinks when cleaning stale docs.

---

# 7. Implementation Requirements

### Requirement 1 — Exact heading layout (frozen by golden tests)

Single-file, Level 0 (`<…>` is substituted; blank lines as shown):

```text
---
generated_by: codewhy v<version>
source: project
generated_at: <ISO 8601, UTC, e.g. 2026-10-11T13:09:00Z>
---

# <root_name> — codebase documentation

<preamble paragraph>

## Overview

<overview text>

### Architecture

<architecture text>

### Entry Point

`<path>`                         (or: No conventional entry point detected.)

### Modules

- `<module path>` — <first sentence of Responsibility>      (module "." is written as `(project root)`)

### Most depended-on files

- `<path>` — imported by <n> file(s)

### Other files in this project

- `<path>`
- … and <N> more

## Modules

### `<module path>`

#### Responsibility

<text>

#### How the pieces cooperate

<text>

#### Files in this module

- `<path>`

#### Internal dependencies

- `<a>` → `<b>`                  (or: None)

#### External dependencies

- `<name>`                       (or: None)

## Files

### `<path>`

#### Purpose

<text>

#### Depends On

- `<path>`                       (or: None)

#### Used By

- `<path>`                       (or: None)

#### Defined Here

- `class Name(Base)`
  - `def method(self, x) -> int`
- `def function(a, b)`
- `interface Name`               (TS types)

#### Notes

<text>                           (section omitted when empty)
```

* **Level 1 (compact):** drops `Notes` and `Defined Here` on every file; everything else as Level 0.
* **Level 2 (minimal):** `## Files` becomes a bullet list `- \`<path>\` — <first sentence of Purpose>`; each module is `### \`<path>\`` followed only by its Responsibility paragraph; the overview keeps `Overview`, `Architecture`, `Entry Point` (drops `Modules`, `Most depended-on files`, `Other files…`).
* **docs-tree:** same section builders with the heading level shifted up one (`heading_offset`), file docs as `# <path>` then `## Purpose` etc., module docs `_module.md` as `# <module path>` then `## Responsibility`…, `PROJECT_OVERVIEW.md` as `# <root_name>` then `## Overview`…; each with its own frontmatter:

```text
---
generated_by: codewhy v<version>
source: <relative path of the original file, or "project">
generated_at: <ISO 8601 UTC>
---
```

  Mirrored structure: `codewhy-docs/<dir>/<filename>.md`, `codewhy-docs/<dir>/_module.md` (root module → `codewhy-docs/_module.md`), `codewhy-docs/PROJECT_OVERVIEW.md`.

### Requirement 2 — Section builders (`render/sections.py`)

Pure functions taking the `ProjectModel`, `RunResult`, and a `level`/`heading_offset`, returning strings; no I/O. LLM text passes through `escape_llm_text` (escape line-leading `#`, normalize `\r\n`, strip trailing whitespace; do not otherwise alter the text). `Defined Here` is built from `FileStructure` (`classes` with methods, `functions`, `types`), signatures verbatim, source order. `[generation failed]` placeholder for failed file/module/project results appears under the corresponding heading.

### Requirement 3 — Single file (`render/single_file.py`) and levels

`render_single_file(project, run_result, config, *, version) -> RenderedDoc(text, level, estimated_tokens, over_budget: bool)`. Render Level 0, estimate with `estimate_tokens`; if over `token_budget` try Level 1, then Level 2; return the first that fits, else Level 2 with `over_budget=True`.

### Requirement 4 — docs-tree (`render/docs_tree.py`)

`render_docs_tree(project, run_result, config, *, version) -> dict[str, str]` mapping output-relative paths to text. Writing/cleanup is done by a small writer that (a) refuses to overwrite a non-codewhy file without `--force`, (b) removes stale per-file docs for files that no longer exist **only** when they carry codewhy frontmatter and lie inside the output dir, (c) never touches other files, (d) never follows symlinks.

### Requirement 5 — `scan` wiring (`cli.py`)

Order of operations (matches the architecture):

1. Resolve config (overrides: `--provider`, `--model`, `--output`; consent flags `--yes`; `--force`, `--quiet`; `--full` accepted and currently a no-op).
2. Build provider; `ensure_remote_ack` (TTY detection via `sys.stdin.isatty()`); `preflight`. Any failure → message, exit 1, **before any file in the project is read**.
3. `build_project` (discovery, extraction, graph, entry point); no supported files → "No supported source files found (supported: .py .js .jsx .mjs .cjs .ts .tsx .mts .cts)" and exit 1.
4. `run_pipeline` with an `on_item` callback printing `[i/N] <pass> <subject> (generated|failed)` unless `--quiet`.
5. Render per `output.mode`; run overwrite checks **before writing anything** so a refusal leaves the tree untouched; write files.
6. Print the summary line: `<files> files, <generated> generated, <failed> failed, <skipped> skipped. <output name> ≈ <tokens> tokens (level <n>).` Warn when `over_budget`. Skip details lines (`skipped <path> (<reason>)`) print before the summary.
7. Exit code per the contract.

`AuthError`/`BadRequestError` from the pipeline → message to stderr, exit 1, nothing written.

### Requirement 6 — Golden files

`tests/golden/` holds the expected single-file output (Levels 0, 1, 2) and one docs-tree for a small scripted project (use `FakeProvider` with scripted responses and a fixed clock and fixed version string). Golden tests fail on any layout change; updating them is an explicit, logged act (provide a documented `--update-golden`-style helper or a script).

### Error Handling

* Unwritable output location → exit 1 with the path and OS error text; no partial tree where avoidable (full atomicity is task 08).
* Markdown-injection attempts in model text (headings, fences) never alter structure.
* An all-failed run still writes the documents (with placeholders), exits 2, and says how many failed.

---

# 8. Testing Requirements

## Automated Tests

* `test_render_sections.py`: `Defined Here` formatting (classes with methods, functions, TS types, source order); `None` rendering; `Notes` omitted when null; `Most depended-on` omitted when empty; `Other files` cap and `… and N more`; `escape_llm_text` (line-leading `#`, fences, CRLF); `[generation failed]` placeholder in the correct section; `(project root)` naming.
* `test_render_single_file.py`: **golden** Levels 0/1/2; preamble text exact; section order; single header block; `generated_at` equals the latest pass result time (and never the wall clock — use `fixed_clock`); render-level selection with a scripted project whose Level 0 is over budget (Level 1 chosen when it fits, else Level 2; Level 2 over budget → `over_budget=True` and a warning); token estimate returned; model text containing `# Heading` does not create a heading; trailing newline.
* `test_render_docs_tree.py`: **golden** tree; mirror structure incl. root `_module.md`; frontmatter parseable on every file; default dir is `codewhy-docs/`; `both` writes both; stale-doc cleanup removes only codewhy-frontmatter files and never a hand-written file in the tree; symlinks never followed.
* `test_scan_integration.py` (FakeProvider via a provider factory seam): default output is `PROJECT_DOCS.md`; overwrite protection (existing non-codewhy file → exit 1, tree unchanged; `--force` overwrites; existing codewhy file overwritten silently); fail-fast ordering (preflight failure before any source file is opened — use unreadable source files); remote provider + no TTY + no `--yes` → exit 1 with zero provider calls; exit code 2 for `broken_files` skips and for any `[generation failed]`; auth error aborts with exit 1 and no output files; no supported files → exit 1 with the message; summary line content and counts; `--quiet` hides item lines but keeps the summary; the run never reads `.gitignore`, never writes outside the root; self-pollution: a second `scan` does not list `PROJECT_DOCS.md` or `codewhy-docs/` in "Other files".
* `test_e2e_live.py` (`@pytest.mark.live_llm`, skipped by default): `scan` on `flat_scripts` with the configured real provider; assert exit 0, every file has a Purpose that is not the placeholder, frontmatter parses, the output is under the default token budget at Level 0, section order correct. Assert structure, not wording.

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q -m live_llm tests/test_e2e_live.py     # only with Ollama/Gemini configured
```

Expected result:

```text
all offline tests pass; the live E2E passes against at least one real provider
```

## Hardware / Model-Dependent Checks

Record each as Passed / Failed / Not run (reason) in `logs/06-render-and-scan.md`:

* The live E2E on `flat_scripts` (Ollama and/or Gemini).
* `codewhy scan` on two of your own real projects; record counts, failures, level chosen, and token estimate.
* **The paste test (the point of the product):** paste `PROJECT_DOCS.md` for one of your projects into a chat model and ask three questions about the codebase; record whether the answers were right and what the document lacked.

## Manual Verification

1. Open the generated `PROJECT_DOCS.md` and read it as a stranger would: is the preamble clear, is the overview useful, are `Used By`/`Depends On` correct against your knowledge?
2. Run `scan` twice and diff the outputs: with no cache they will differ in prose; confirm structure/ordering of deterministic sections is identical.
3. Pre-create a hand-written `PROJECT_DOCS.md` and confirm the refusal and `--force` behavior.

---

# 9. Acceptance Criteria

* [ ] `codewhy scan` works end to end on `flat_scripts` and your real projects.
* [ ] Output matches the frozen layout; golden tests pass for all levels and docs-tree.
* [ ] Render levels select by the estimated token size; the CLI prints level and estimate.
* [ ] Overwrite protection, stale-doc cleanup, and symlink safety behave per §6.
* [ ] Fail-fast ordering, consent, and exit codes verified by tests.
* [ ] Failed items are visible as `[generation failed]`; model text can never break structure.
* [ ] The paste test was performed and its result recorded honestly.
* [ ] `git diff` reviewed; work log updated.
* [ ] If a commit was authorized: staged diff reviewed, only intended files, no AI attribution, no push/PR.

---

# 10. Known Risks

* **Golden files freeze mistakes.** Review the Level 0 golden output by eye before committing it; a wrong layout locked in by tests is expensive to change.
* **Token estimate vs. real tokenizer.** The printed token figure is an estimate; label it `≈` and never claim exactness.
* **Level 2 can be too thin to be useful.** If real projects routinely hit Level 2, the budget or the level definitions need rethinking (log evidence; do not silently change the contract).
* **Windows console encoding** (`—`, `→`, `…`) can raise on legacy code pages; ensure terminal output degrades safely and file output is always UTF-8 with `\n`.
* **No cache yet:** repeated runs cost full price; fine for this task, and a reason not to hand this build to anyone before task 07.
* Escaping line-leading `#` must not mangle legitimate prose such as an issue number at line start; keep the escape minimal (a backslash before the `#`) and test it.

---

# 11. Open Questions

* **requires clarification:** Should `Modules` in the overview show the first sentence of Responsibility or the module's file count too? — Recommended: first sentence only (as specified).
* **requires clarification:** `--output both` when a user's `codewhy-docs/` already has hand-written files? — Recommended: write only generated files and never delete non-codewhy files (as specified).

---

# 12. Handoff Notes

* Task 07 plugs the cache into `run_pass` and makes `generated_at` persist, enabling byte-identical re-runs; it also adds the `cached` status to the item callback and summary counts.
* Task 08 adds `--dry-run`, richer progress, atomic writes, interrupt handling, and the failure-boundary matrix on top of this `scan`.
* Record in the log: any layout deviations (and the architecture edit that records them), paste-test results, observed token sizes per level on real projects, and the golden-update procedure.

If the task is complete:

> No outstanding handoff notes.

---

# 13. Definition of Done

See `CLAUDE.md` → *Definition of done*. Do not commit, push, open a PR, or merge unless the user explicitly authorizes that specific action.

## Final Agent Instruction

Before making changes: read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, this file, and the logs of tasks 01–05; run `git status`; write your plan into `logs/06-render-and-scan.md`.

During implementation: freeze the layout with golden tests only after reviewing it by eye; stay in scope; do not invent architecture; never write outside the scanned folder.

If the task requires something not covered here, stop and ask the project lead.

Before handing off: run the tests, run the live checks you can (record the rest as Not run), review `git diff` and `git status`, update the log, and report what changed, what was verified, and what is untested or unresolved.
