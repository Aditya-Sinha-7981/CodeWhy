# Testing Strategy

Companion to `ARCHITECTURE.md`. Goal: catch regressions in the two parts of
this system that are easy to get subtly wrong (cache cascade logic, LLM
context handling) before they show up as "the docs are stale/wrong" in a
demo, not after.

## Test pyramid

```
        ┌───────────────────────┐
        │  Fixture-repo E2E (5) │   full `codedoc scan` runs, real Ollama
        ├───────────────────────┤
        │  Integration (~15)    │   multi-module pipelines, mocked LLM
        ├───────────────────────┤
        │  Unit (~40+)          │   one function/class per test, fast, no I/O
        └───────────────────────┘
```

**Rule:** anything that calls Ollama is either mocked (unit/integration) or
explicitly marked `@pytest.mark.live_llm` and skipped by default (E2E) so
the suite runs fast without a running Ollama daemon. Only the E2E layer
hits the real model.

Tooling: `pytest`, `pytest-mock` for the Provider interface, no other
framework needed at this scale.

## Fixture repos (`tests/fixtures/`)

Build these once in Phase 1, reuse through every later phase:

| Fixture | Purpose |
|---|---|
| `tiny_python/` | 3 files, no subfolders, one has a syntax error | single-file mode trigger, `ast` error handling |
| `python_js_mixed/` | Python backend + JS frontend, imports on both sides | multi-language extraction, cross-language graph (should NOT create edges between languages — imports don't resolve across them) |
| `nested_modules/` | 3+ folders deep, 15+ files | docs-tree mode trigger, module_pass grouping, project_pass rollup |
| `oversized_file/` | one file with a function long enough to exceed context | chunking |
| `ignored_content/` | includes a `node_modules/`, a `.venv/`, and a custom pattern in `.toolignore` | ignore-list correctness |

## Layer 1 — Extraction

- `test_python_ast.py`: function/class/method extraction correctness,
  docstring capture, relative vs absolute import distinction, line count.
- `test_ts_treesitter.py`: same assertions, JS and TS both, JSX doesn't
  break parsing.
- **Regression case (locked):** malformed Python file → extractor returns
  a `skipped` result with a warning, does not raise — this is a failure
  boundary from the architecture doc, test it directly, don't just trust
  the try/except exists.
- Determinism check: run extraction twice on the same file, assert
  byte-identical output. This is the property the whole cache system
  depends on — worth a named test, not just an assumption.

## Layer 2 — Dependency graph

- Relative imports become edges; third-party imports become node metadata,
  not edges.
- `python_js_mixed` fixture: assert zero edges between the Python and JS
  file sets — cross-language "edges" would be a bug, not a feature, in v1.
- Cycle handling: two files importing each other doesn't crash graph
  construction (networkx `DiGraph` allows cycles; just confirm nothing
  downstream assumes a DAG).

## Layer 3 — Cache / smart cascade

This is the layer most likely to have a subtle bug, so it gets the most
scenario coverage. Each test below sets up `file_state`/`module_state`
rows directly (not via a full run) so the cascade logic is tested in
isolation from Layers 1/2/4.

| Scenario | Expected outcome |
|---|---|
| File content changes, resulting summary text is identical to before | `file_pass` re-runs (source changed), `module_pass` does **not** re-run (summary_hash unchanged) |
| File content changes, resulting summary text differs | `file_pass` re-runs, `module_pass` re-runs, and `project_pass` re-runs only if the module's summary also changed |
| Two files in the same module both change, only one produces a new summary_hash | module still recomputes `member_summary_hashes` and re-runs (any single differing member triggers it) |
| No files changed at all | zero LLM calls made, all cached values returned as-is |
| `--full` flag passed | every LLM call happens regardless of hashes, cache table fully rewritten |
| Cache DB file present but corrupted (garbage bytes) | treated as cache-empty, full regenerate, warning logged, run does not crash |
| First-ever run (no cache DB) | same as corrupted case — full regenerate, no error |

## Layer 4 — Summarization

- Provider interface: `OllamaProvider` mocked via `pytest-mock` for all
  non-E2E tests — never hit a real model outside the E2E suite.
- **Context-window regression test (this is the risk flagged in
  conversation, test it explicitly):** assert that every call through
  `ollama_provider.py` passes an explicit `num_ctx` — write a test that
  fails if a call is made without one, so nobody accidentally relies on
  Ollama's silent 2048-token default truncating input.
- Chunking: `oversized_file` fixture — assert the file gets split along
  function/class boundaries (not mid-function), each chunk summarized,
  final combine call happens exactly once.
- **Chunking edge case (second risk flagged in conversation):** a single
  function whose body alone exceeds context. Decide the fallback now
  rather than discovering it live — recommended: truncate that function's
  body with a `[...truncated, N lines omitted]` marker rather than
  failing the whole file. Write the test against whichever fallback you
  pick.
- Malformed/empty LLM response → retry once, then `[generation failed]`
  placeholder per the failure-boundary table — test both the retry and
  the eventual placeholder path.

## Layer 5

Not implemented in v1 — no tests. Confirm `metadata` is always `{}` in the
return shape via one placeholder unit test, so a future PR touching this
seam has a green baseline to work from.

## Rendering / output

- `docs_tree.py`: output folder structure mirrors source structure,
  frontmatter present and well-formed on every generated file.
- `consolidated.py`: single file, correct section ordering (overview →
  modules → files), no per-section frontmatter duplication.
- Mode selection: `nested_modules` fixture (15 files) → docs-tree by
  default; `tiny_python` fixture (3 files) → single-file by default;
  both overridable by `--single-file`/`--docs-tree` regardless of count.

## Ignore handling

- Built-in list always applied even with no `.toolignore` present.
- `.toolignore` patterns extend, never replace, the built-in list.
- `.gitignore` presence has zero effect on output — test this directly
  since it's a locked decision someone could "helpfully" undo later.

## CLI

- `codedoc init` writes `codedoc.toml` and `.toolignore` without
  overwriting existing ones (should prompt or no-op, not clobber).
- `codedoc config show` reflects overrides from an actual `codedoc.toml`,
  not just defaults.
- Missing Ollama daemon / missing pulled model → clear, specific error
  message before any extraction work starts (fail fast, per the failure
  boundary table) — test the message content, not just that it errors.

## E2E (fixture-repo, real Ollama, marked `live_llm`)

Run `codedoc scan` end-to-end on each fixture repo. Not asserting exact
summary text (LLM output isn't deterministic) — asserting structure:

- Exit code 0
- Every source file has a corresponding output section/file
- Frontmatter present and parseable
- Re-running immediately after with no source changes triggers zero LLM
  calls (cache correctness, observed via a call-counting wrapper around
  the real provider)
- Modifying one file and re-running only regenerates what the smart
  cascade says it should

## Mapping to build phases

| Phase | Tests to have green before moving on |
|---|---|
| 1 | Extraction + graph unit tests, determinism check |
| 2 | `file_pass` unit tests (mocked), first E2E smoke test on `tiny_python` |
| 3 | Full Layer 3 cascade table, `module_pass`/`project_pass` unit tests, both output-mode tests |
| 4 | Chunking tests (both cases), context-window regression test, ignore-handling tests, CLI tests |
| 5 | Full E2E suite across all fixtures, `pipx install` and `uvx` smoke-tested manually (not unit-testable) |

## Explicitly not covered

- Summary *quality* (is the explanation actually good) — no automated
  test for this, it's a human judgment call during your own review of
  each phase's demo output.
- Performance/timing benchmarks — out of scope for a semester project
  unless a professor specifically asks for them.
