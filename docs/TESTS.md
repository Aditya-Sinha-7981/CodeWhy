# codewhy — Testing Strategy

Companion to `ARCHITECTURE.md` (the source of truth; if a test and the
architecture disagree, fix whichever is wrong in the same commit). Work orders
in `tasks/` say which of these tests each task must deliver.

Goals:
1. Catch regressions in the parts that are easy to get subtly wrong: import
   resolution, cache keys, prompt budgeting, chunking.
2. Prove the product claims in `ARCHITECTURE.md` → *Product goals* (one command,
   pasteable output, cheap re-runs, no surprises) with tests, not assertions.
3. Show with evidence that the generated docs actually help (see *Quality
   evaluation*).

## Test pyramid

```
        ┌──────────────────────────┐
        │ Fixture E2E (5, live_llm)│   real model, structure-only assertions
        ├──────────────────────────┤
        │ Integration (~20)        │   multi-pass pipelines, mocked provider
        ├──────────────────────────┤
        │ Unit (~70+)              │   one function/class per test, no I/O or tmp_path only
        └──────────────────────────┘
```

**Rule:** nothing outside the E2E layer talks to a real model or network.
E2E tests are marked `@pytest.mark.live_llm` and skipped by default. A
`FakeProvider` (deterministic, records every call, can be scripted to return
bad JSON, fail transiently, or echo a fixed schema-valid result) is the default
test double; use `pytest-mock` only where patching is simpler than the fake.

Tooling: `pytest`, `pytest-mock`. No other test frameworks.

## Helpers

- `make_project(tmp_path, {"main.py": "...", "pkg/__init__.py": "", ...})` —
  builds a throwaway tree in code. Use it for one-off cases (entry-point
  priority, module-definition edges, import-resolution cases) instead of
  committing a fixture directory for each.
- `FakeProvider` — as above. Exposes `.calls` (prompt, schema, options) so tests
  can assert on what was sent.
- `run_scan(project, **flags)` — invokes the CLI in-process and returns exit
  code, stdout, and the written files.
- `fixed_clock` — an injectable clock so `generated_at` values are
  deterministic in golden-file and idempotence tests.

## Fixture projects (`tests/fixtures/`)

Two kinds, because some fixtures cannot be committed to git:

- **Committed directories:** `flat_scripts/`, `python_js_mixed/`,
  `nested_modules/`.
- **Built in code** by functions in `tests/fixtures/builders.py` (each takes
  `tmp_path` and returns the project root): `build_ignored_content`,
  `build_broken_files`, `build_oversized_file`. Reasons: the repo's own
  `.gitignore` would swallow `node_modules/`, `.venv/` and `__pycache__/`;
  symlink loops and invalid-UTF-8 files are fragile in git and on Windows; the
  oversized file's size must be parameterized to the test's `context_tokens`.

Each fixture is created in the task that first needs it (shown in the last
column), with realistic code rather than lorem ipsum.

| Fixture | Contents | Used for | Task |
|---|---|---|---|
| `flat_scripts/` | 3 files, no subfolders: `main.py`, `solver.py`, `utils.py` (a small backtracking solver), using **absolute** local imports (`import utils`, `from solver import solve`) — the motivating "student assignment" shape | Python absolute-import resolution (the core use case), single-file default, primary happy-path E2E | 02 |
| `python_js_mixed/` | Python backend + JS/TS frontend (ESM, CommonJS, `.tsx`, `index` imports, `.js`→`.ts` specifiers, an alias import, a `.css` import) | multi-language extraction; **zero** cross-language edges; JS resolution | 02 |
| `nested_modules/` | 15+ files, 3+ folders deep, `src/` layout, relative imports, `__init__.py` packages, a folder containing only subfolders | module definition, relative resolution, module/project grouping, docs-tree output | 03 |
| `ignored_content` (built) | `node_modules/`, `.venv/`, a custom `.toolignore` pattern, a `.gitignore` that would hide a file the user wants documented, a user-owned `build/` re-included via `!build/` | ignore handling | 01 |
| `broken_files` (built) | a Python file with a syntax error, an empty `__init__.py`, a file with invalid UTF-8 bytes, a supported file larger than a test-lowered `max_file_bytes`, a symlink loop, plus two healthy files | skip/warn behavior, exit code 2, empty-file shortcut, symlink safety | 02 |
| `oversized_file` (built) | one file with many medium functions, one class larger than a chunk, one single function larger than a chunk; sizes scale with a `chunk_tokens` argument | chunking (all three cases) | 05 |

## Discovery and ignore (`ignore.py`, `project.py`)

- Built-in list always applied with no `.toolignore` present.
- `.toolignore` patterns extend the built-ins; a `!build/` line re-includes a
  built-in-ignored folder.
- `.gitignore` presence has **zero** effect (test directly — it is a locked
  decision someone could "helpfully" undo).
- Always-excluded artifacts (`.codewhy/`, output dir, `PROJECT_DOCS.md`,
  `codewhy.toml`, `.toolignore`) never appear as source or in "Other files",
  even when a `.toolignore` tries to negate them.
- **Self-pollution regression:** run a scan, run discovery again — the second
  discovery's file set (and "Other files") is identical to the first.
- Ignored directories are pruned (assert the walker never lists
  `node_modules/` contents — use a tree with a deliberately unreadable entry
  inside).
- Symlinks are not followed (loop fixture terminates; link target outside the
  root is not read).
- Supported-extension mapping covers every extension in the architecture,
  including `.mjs .cjs .mts .cts`; `*.d.ts` is ignored.

## Module definition and project model

- Folder with ≥1 supported file → module; folder with only subfolders → no
  module; root with source files → module `.`.
- Non-recursive: a file in `a/b/` belongs to `a/b`, not `a`.
- Folder with a single supported file still gets its own module.
- Unsupported files are listed (path only), capped at 100 with `… and N more`.

## Entry-point detection

Parametrized over `make_project` trees; one test per priority edge:

- Explicit `entry_point` wins over everything; nonexistent explicit path is a
  fatal config error.
- `pyproject.toml` `[project.scripts]` `pkg.mod:func` resolves through the
  Python resolver; first resolvable entry wins; unresolvable entries fall
  through to the next source.
- `[tool.poetry.scripts]` consulted after `[project.scripts]`.
- `package.json` `main` resolves with JS rules; `main` pointing at an ignored or
  missing file (e.g. `dist/index.js`) falls through.
- `scripts.start` handled only for `node|ts-node|tsx|nodemon [flags] <path>`;
  `react-scripts start` and `npm run x` are not matched.
- Conventional filenames honored in the exact documented order.
- Nothing matches → `None`.

## Layer 1 — Extraction

- `test_python_ast.py`: functions, classes, methods, signatures, docstrings,
  relative vs absolute imports (with level), line count; imports found inside
  functions, `try/except ImportError`, and `if TYPE_CHECKING`.
- `test_ts_treesitter.py`: same assertions for JS and TS; `.jsx`/`.tsx` parse;
  static `import`, `export … from`, `require('x')`, `import('x')` collected;
  non-literal `require(x)` / `import(x)` ignored; arrow-function consts and
  class methods extracted.
- `top_level_units`: contiguous coverage of the whole file (property test: unit
  ranges have no gaps or overlaps and span line 1..N); leading imports and
  module-level statements attach to the following unit; class units carry
  method children.
- **Failure boundary (locked):** malformed source → `status="skipped"` with a
  warning, never raises. Test the extractor directly; don't just trust the
  try/except exists.
- **Determinism (named test):** extract twice, assert byte-identical
  serialized output. The cache depends on this property.

## Layer 2 — Resolution and graph

Python resolution (each a small `make_project` case):
- flat script: `import utils` in `main.py` → edge to `utils.py` (the headline
  case; must pass before anything else in task 03 is considered done).
- `from solver import solve` → edge to `solver.py`.
- Relative: `from . import x`, `from .a import b`, `from ..pkg import c`
  (levels 1 and 2), package `__init__.py` targets, submodule preferred over
  package file for `from a.b import c`.
- Absolute import resolved from `R/src` in a `src/` layout.
- Longest-prefix rule: `import a.b.c` where only `a/b.py` exists → edge to
  `a/b.py`.
- Search-root order: a local `D/utils.py` beats `R/utils.py`.
- `import os`, `import numpy` → `external_imports` metadata, **no** edge.
- Unresolvable relative import (`from . import missing`) → `unresolved_imports`,
  not an edge, not an external dependency.
- Self-import → no edge. Duplicate imports → one edge.

JS/TS resolution:
- `./foo` → `foo.ts` before `foo.js` (extension order); `./dir` → `dir/index.*`.
- Exact-extension specifier; TS ESM `./foo.js` resolves to `foo.ts` when
  `foo.js` doesn't exist.
- Bare (`react`) and alias (`@/utils`) specifiers → external, no edge
  (documented limitation, test it so it's a known behavior).
- Relative specifier to a non-source file (`./styles.css`, `./data.json`) →
  `unresolved_imports`, no edge, not external.

Graph:
- `python_js_mixed`: assert zero edges between the Python and JS/TS file sets.
- Cycle (A imports B imports A) builds without error; nothing downstream
  assumes a DAG.
- Canonical edge list: `/` separators, sorted, stable across runs and across
  insertion order; a module's relevant edges include edges where *either*
  endpoint is in the module.
- `Used By` / `Depends On` / top-5 most-depended-on are correct and sorted;
  zero in-degree files excluded from the top list.

## Layer 3 — Cache

Tests build `pass_cache` rows directly and compute `input_key` via
`cache/keys.py`, so the cache logic is tested in isolation from the passes. The
scenarios below all use `FakeProvider` and count calls.

| Scenario | Expected outcome |
|---|---|
| No files changed | **Zero** provider calls; all results from cache; output files byte-identical to the previous run |
| File source edited; model returns identical Purpose text | file pass re-runs; module pass **not** re-run (module key unchanged); project pass not re-run |
| File source edited; Purpose text differs | file pass and that file's module pass re-run; project pass re-runs only if that module's `Responsibility` text differs |
| Only a file's `notes` changes | module pass **not** re-run (notes don't enter module inputs) |
| Import edge changes (`A→B` becomes `A→C`), all summary texts identical | affected file prompts (`imports`/`imported_by`), the relevant module keys, and the project key change; those passes re-run |
| New file added to a module | module key changes (member set changed); module pass re-runs |
| File deleted | its `pass_cache` row is removed after the run; module re-runs if it had other members; a module left with no files has its row removed |
| File renamed (same content) | old row removed, new row created; neighbors' keys change via paths |
| Entry point changes | project key changes; project pass re-runs, nothing else |
| `config_fp` change (model, `context_tokens`, temperature, seed) | everything re-runs |
| `PIPELINE_VERSION` bumped | everything re-runs |
| `--full` | every pass calls the provider regardless of keys; rows rewritten |
| `[generation failed]` result | not written to cache; next run retries it |
| Run interrupted after N upserts (raise from FakeProvider on call N+1) | next run makes only the remaining calls (resume) |
| Cache DB contains garbage bytes | treated as empty, warning logged, run completes, DB rebuilt |
| Unknown `schema_version` in `meta` | same as corrupted |
| First-ever run (no DB) | full generation, no error, DB created |

Also: key stability — the same inputs in a different dict/list insertion order
produce the same `input_key`.

## Budgeting and prompt assembly (`llm/budget.py`, `prompts.py`)

- `estimate_tokens` is deterministic and monotonic in length.
- `prompt_budget` formula: with `context_tokens=16384`,
  `max_output_tokens=1024` → `floor(15360 * 0.9)`.
- **Invariant property test:** across every fixture and every pass, with
  several `context_tokens` values (including a deliberately tiny one), every
  prompt handed to the provider satisfies
  `estimate_tokens(prompt) <= prompt_budget`. This is the real guard against
  silent truncation.
- Source inclusion: a file whose full prompt fits gets full source (assert on
  the recorded prompt); there is no line-count threshold (a 400-line file that
  fits still gets its source).
- `file_pass` prompt contains the file's `imports` and `imported_by` paths and
  does **not** contain neighbor summaries.
- Every pass prompt contains the "Do not invent behavior…" instruction
  verbatim.
- Project prompt with `entry_point=None` explicitly instructs the model to say
  no conventional entry point was detected; with an entry point, it contains
  that path.
- Overflow ladders (module and project): build a module with enough files that
  full Purposes overflow → assert step 2 (first sentences) is used and fits;
  enough that step 2 overflows → assert step 3 (in-degree ordering plus the
  `N more … omitted` line) and that the omitted count is correct.

## Providers

`ollama_provider.py` (HTTP layer mocked):
- **Context-window regression (named, locked):** every request carries
  `options.num_ctx == context_tokens`; a test fails if any code path builds a
  request without it.
- Request carries `format` (the pass JSON schema), `temperature`, `seed`.
- Preflight: unreachable host → `ProviderError` whose message contains
  `ollama serve`; model missing → message contains `ollama pull <model>`.
  Assert on message **content**, not just that it raises.
- Loopback detection: `localhost`, `127.0.0.1`, `::1` local; anything else
  remote.

`openai_compat_provider.py` and the `gemini` preset (HTTP layer mocked):
- `gemini` preset fills `base_url`, `api_key_env=GEMINI_API_KEY`,
  `structured_output=schema`, `reasoning_effort=low`, `send_seed=false`,
  `context_tokens=32768`; user overrides win; missing `model` is a clear error.
- Request body: `temperature` and `max_tokens` always present; `seed` only when
  `send_seed`; `reasoning_effort` absent when unset; `response_format` is
  json_schema / json_object / absent for `schema` / `json` / `prompt`.
- Provider-facing schema helper: no `$defs`, no `anyOf`, optional fields typed
  as plain strings, required list preserved.
- Preflight: 401/403 → auth message; 404/405/501 on `/models` → check skipped;
  connection error → unreachable message; model missing from a successful
  listing → warning only, not failure.
- Error mapping: 401 never retried; 400 produces `BadRequestError` with the key
  redacted from the body excerpt; unparseable content → `MalformedOutput`.
- Sends bearer key read from the env var at call time; key never appears in
  logs, the cache DB, or any generated file (grep the whole tmp tree in the
  test).
- Missing env var → clear error naming the variable, before any call.
- Uses `response_format` json_schema when enabled; falls back to JSON-only
  prompting and extracts the first JSON object from noisy output.
- 429 with `Retry-After` is honored (inject a fake sleep); backoff stops after
  `max_transient_retries`; 401 is not retried.

Both: response is validated against the pass pydantic model regardless of
native structured-output support.

## Layer 4 — Summarization

- **Malformed output:** unparseable JSON, schema-invalid JSON, and empty
  required string each trigger exactly one retry; the retry prompt contains the
  validation error text; a second failure yields the `[generation failed]`
  result and the run continues. Test both the retry and the placeholder path.
- Transient provider failure exhausted → `[generation failed]`, run continues.
- Empty-file shortcut: no provider call, purpose is `Empty file.`.
- Schemas: `file` result with `notes=None` renders no `Notes` section.

### Chunking (`oversized_file`)

- Split points fall on `top_level_units` boundaries only — property test: no
  chunk starts or ends mid-unit.
- Greedy packing: every chunk prompt fits the budget; consecutive units; every
  unit appears in exactly one chunk.
- Each chunk prompt includes the file's full top-level name list.
- Class larger than a chunk → split at method boundaries (header + methods).
- **Single function larger than a chunk (locked fallback):** body truncated to
  head + `[...truncated, N lines omitted]` with the correct N; the file still
  succeeds.
- Exactly **one** combine call after the chunk calls; if the combine prompt
  would overflow, chunk summaries are cut to first sentences first.
- Chunk calls are not cached individually; the cache holds one row for the
  file.

## Layer 5

Not implemented in v1. One placeholder unit test: every pass returns a
`PassResult` whose `metadata == {}`, so a future change to this seam starts
from a green baseline.

## Rendering / output

- docs-tree: output folder structure mirrors source; `_module.md` placement
  (including root); `PROJECT_OVERVIEW.md` present; frontmatter present and
  parseable on every generated file; default directory is `codewhy-docs/`, not
  `docs/`.
- single-file: section order is preamble → overview → modules → files; one
  header block, no per-section frontmatter; the preamble is present and
  identical across runs.
- **Idempotence (named):** two consecutive scans with no source changes
  produce byte-identical output files (this is what proves
  `generated_at`-from-cache-rows works).
- **Render levels:** with a scripted project whose Level 0 output is estimated
  over `token_budget`, Level 1 is chosen when it fits, else Level 2; Level 2
  over budget still emits and warns; chosen level and token estimate are
  printed. docs-tree is always Level 0.
- Markdown injection: LLM text containing line-leading `#` headings does not
  create headings in the output.
- `[generation failed]` renders as a visible placeholder in the right section.
- Stale docs: in docs-tree mode a deleted source file's doc is removed, but only
  if it carries codewhy frontmatter; a hand-written file in the tree is never
  touched.
- Overwrite protection: existing `PROJECT_DOCS.md` without codewhy frontmatter
  → exit 1 and file unchanged; with `--force` → overwritten; with codewhy
  frontmatter → overwritten silently.
- `Other files` list excludes codewhy's own outputs.

## CLI

- `init` writes `codewhy.toml` and `.toolignore`; running it again skips both
  and says so (never clobbers).
- `config show` reflects values from a real `codewhy.toml` and from CLI
  overrides, not just defaults.
- `doctor`: prints the exact fix for each preflight failure; exits 0 when
  healthy; makes no writes.
- `scan` default `PATH` is `.`; `--provider` / `--model` override config (also
  on `doctor`); an unknown provider name is a clear exit-1 error.
- `inspect` prints deterministic JSON (snapshot test on `nested_modules`),
  contains no `source_text`, makes no provider call, writes nothing.
- **`--dry-run`:** zero provider calls (assert on `FakeProvider.calls`), zero
  files written (snapshot the tree before/after, including `.codewhy/`),
  prints discovered/skipped files, cache hits/misses, estimated calls and
  tokens.
- **Exit codes:** 0 clean; 2 when `broken_files` skips files or any
  `[generation failed]` occurs; 1 for provider-down, model-missing, bad
  config, unacknowledged remote endpoint, refused overwrite.
- **Fail-fast ordering:** preflight failure happens before any extraction
  (assert that no source file was opened — use a project whose files are
  unreadable).
- **Remote acknowledgement:** remote endpoint + no TTY + no `--yes` /
  `acknowledge_remote` → exit 1 with the message and **zero** provider calls;
  `--yes` proceeds; interactive "n" aborts; local Ollama never prompts.
- Progress lines show `(cached)` vs `(generated)`; `--quiet` suppresses them
  but still prints the summary line.
- Run summary line reports correct counts and the estimated output tokens.

## E2E (committed fixtures, real model, `@pytest.mark.live_llm`)

Run `codewhy scan` on `flat_scripts`, `nested_modules`, `python_js_mixed`,
`oversized_file`, and `broken_files`. Assert **structure**, not wording:

- Exit code 0 (2 for `broken_files`, with the expected skips reported).
- Every successfully parsed source file has a section/file in the output.
- Frontmatter present and parseable; no `[generation failed]` on healthy files.
- `PROJECT_DOCS.md` for `flat_scripts` is under the default token budget at
  Level 0.
- Re-running immediately triggers zero provider calls (call-counting wrapper)
  and produces byte-identical output.
- Editing one file and re-running regenerates only what the cache rules in
  `ARCHITECTURE.md` say it should.

## Quality evaluation (manual, scripted — this is your evidence)

Automated tests prove the tool is *correct*; this section shows it is
*useful*. No automated pass/fail — it feeds your report and README.

1. **Corpus:** 3–4 real projects of different shapes — at least two of your own
   messy ones, plus one or two small open-source projects you did not write.
2. **Questions:** for each, write 8–10 questions a newcomer would ask ("where
   is X handled?", "what happens if Y fails?", "which file would I change to
   add Z?"). Write the ground-truth answers by reading the code yourself.
3. **Conditions:** ask the same questions to the same chat model with (a) only
   the file listing, (b) the raw concatenated source when it fits, (c)
   `PROJECT_DOCS.md`. Score each answer correct / partly / wrong, blind to
   which condition produced it where practical.
4. **Report:** a table of scores per condition per project, plus the token size
   of each context. The interesting claim is (c) ≈ (b) quality at a fraction
   of the tokens, and (c) ≫ (a).
5. **Mention check (`eval/check_mentions.py`):** extract every backticked
   identifier and file path from the generated prose and report the
   percentage that exist in the extraction output. Report it as a metric (not a
   gate in v1); it quantifies hallucination and is the seed of the v2
   grounding pass.

Script the runner (`eval/run_eval.py`) so the table can be regenerated when
prompts change — prompt edits are the main source of quality regressions and
nothing else in this suite will catch them.

## CI (task 09; the wheel-matrix check lands earlier, in task 02)

GitHub Actions matrix: macOS, Linux, Windows × Python 3.11, 3.12, 3.13 running
unit + integration tests (no live LLM). A separate job builds the wheel and, in
a clean environment, runs `uvx --from <wheel> codewhy --help` and `pipx
install <wheel>` followed by `codewhy scan` on `flat_scripts` against a stub
Ollama-API HTTP server (`tests/support/stub_ollama.py`, started by the job — no
test-only hooks in production code). This is what keeps the "one command" claim
honest on other people's machines.

## Mapping to tasks

| Task | Tests green before moving on |
|---|---|
| 01 | Discovery/ignore (incl. `ignored_content`, self-pollution regression), module definition, config loading/validation, `init`, `config show` |
| 02 | Extraction (Python + TS/JS, determinism, `top_level_units` property, skipped-file boundary, `broken_files` skips), wheel check green on the three OSes |
| 03 | Resolution (flat-script case first), graph, canonical edges, derived data, entry-point detection, `inspect` snapshot |
| 04 | Budget, schema helper, Ollama + openai-compat + gemini provider tests (incl. `num_ctx` regression, preflight messages), remote-ack logic, `doctor`, live provider smoke (user-run) |
| 05 | Prompt tests, `file_pass`/`module_pass`/`project_pass`, budget invariant property test, chunking (all cases), overflow ladders, retries, orchestrator, live pass check |
| 06 | Render tests (both modes, levels, golden files, injection, overwrite protection, stale-doc removal), real `scan` integration tests, first live E2E on `flat_scripts` |
| 07 | Full cache scenario table, idempotence, resume, corrupt-DB handling |
| 08 | `--dry-run`, progress, exit codes, every failure-boundary row (matrix test), atomic writes, interrupt handling |
| 09 | Full E2E suite, CI matrix green, wheel smoke test, quality evaluation run and written up |

## Explicitly not covered

- Summary *wording* quality in unit tests — only the manual evaluation above
  judges that.
- Performance/timing benchmarks, unless a reviewer asks (the token and call
  counts in `--dry-run` are the cost story).
- Secret redaction (deferred feature).
