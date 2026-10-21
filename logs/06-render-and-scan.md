# Work log — CW-06 render and scan

## 2026-10-21 — implementation plan

Task 6 is being implemented as a single-owner stream covering rendering,
safe output writing, scan CLI integration, tests, manual validation, and this
work log. The current branch is `task/06-render-scan`; the initial worktree was
clean and no pre-existing changes were found.

Implementation order:

1. Add pure rendering foundations and section tests.
2. Add single-file rendering, render levels, timestamps, and golden tests.
3. Add docs-tree rendering and safe output writing/cleanup tests.
4. Replace the scan discovery stub with provider/preflight/pipeline/render
   wiring and integration tests. Remove only the obsolete tests that assert the
   old scan stub behavior; retain unrelated CLI/config coverage.
5. Run focused checks, full offline regression, and available live/manual
   checks. Record every hardware/provider-dependent check as Passed, Failed, or
   Not run with a reason.
6. Review the complete diff and final Git state without committing or pushing.

Contracts to preserve:

- `ProjectModel`, `RunResult`, `PassResult`, extraction structures, and graph
  helpers remain the renderer's data sources.
- Rendering is deterministic and has no provider, filesystem, or wall-clock
  dependency.
- Generated text is UTF-8 with LF newlines and exactly one trailing newline.
- Single-file levels are selected by `estimate_tokens`; docs-tree is Level 0.
- Consent and provider preflight happen before project discovery/extraction.
- Output targets are validated before any write; handwritten files are
  protected unless `--force`; stale cleanup is CodeWhy-frontmatter-only and
  symlink-safe.
- Task 6 does not add caching, functional `--full`, `--dry-run`, richer
  progress, interrupt handling, full atomic-write guarantees, new providers,
  or unrelated refactoring.

The architecture's idempotence behavior depends on Task 7 cache persistence;
Task 6 will verify deterministic structure and fixed-clock rendering without
implementing caching.

## Implementation results

### Automated checks

- **Passed** — `.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-task6 tests\test_render_sections.py tests\test_render_single_file.py tests\test_render_docs_tree.py tests\test_scan_integration.py` — 19 passed.
- **Passed** — `.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-task6` — 644 passed, 5 skipped, 1 Windows symlink-privilege warning.
- **Passed** — `git diff --check` after implementation cleanup.

### Live and manual checks

- **Not run (timed out)** — `.venv\Scripts\python.exe -m pytest -q -m live_llm tests\test_e2e_live.py --basetemp .pytest-task6-live`; Ollama and `qwen2.5-coder:14b` were available, but local generation produced no result after approximately two minutes and was stopped.
- **Not run (no user project paths supplied in this session)** — scan of two real projects.
- **Not run (requires a completed real-project document and chat model)** — three-question paste test.
- **Not run (depends on the unavailable completed live/manual run)** — stranger-read review and repeat-scan comparison.

### Deviations and compatibility updates

- Removed only obsolete scan-stub assertions from `tests/test_cli_init_config.py`,
  `tests/test_doctor.py`, and `tests/test_inspect.py`. Their remaining config,
  doctor, and inspect coverage is preserved.
- No architecture change was required.
- No cache, functional `--full`, `--dry-run`, richer progress, interrupt
  handling, full atomic-write guarantee, new provider, or unrelated refactor
  was added.

### Final regression and review

- **Passed** — final `.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-final` — 644 passed, 5 skipped, 1 Windows symlink-privilege warning.
- **Passed** — final `git diff --check`.
- Final status intentionally remains uncommitted for review. Changed paths are
  limited to the Task 6 renderer, scan CLI, renderer/integration/golden/live
  tests, obsolete scan-stub test cleanup, and this work log.

## 2026-10-21 — scan performance optimization

### Diagnosis

`D:\SISTEC\DocuTrust` was available for measurement. It contains 54,382 total
files, 181 discovered supported files, 27 modules, 771 other files, 302 graph
edges, and no discovery skips. With `FakeProvider`, the local stages measured:

```text
discovery  0.078s
extract    0.445s
graph      0.019s
entrypoint 0.001s
pipeline   0.205s
render     0.006s
total      0.754s
```

The pipeline made 200 provider calls (empty-file shortcuts account for the
difference from 208 generated results). The bottleneck for a real scan is the
sequential provider generation, not parsing, graph construction, rendering, or
writing. No cache implementation exists in this worktree; caching remains
Task 7 scope.

### Optimization

- Added bounded two-worker execution for independent file and module passes.
- File passes remain a barrier before module passes; modules remain a barrier
  before the sequential project pass.
- Only `OllamaProvider` and `OpenAICompatProvider` opt in, because their shared
  `httpx.Client` and per-request body/retry state support concurrent requests.
- FakeProvider and providers without the opt-in capability remain sequential.
- Results, dictionaries, progress callbacks, and rendering order remain sorted.
- No new dependency, cache, provider, configuration key, or architecture
  contract was added.

### Verification

- **Passed** — focused provider/pipeline/scan tests: 98 passed.
- **Passed** — full suite: 647 passed, 5 skipped, 1 Windows symlink-privilege
  warning.
- **Passed** — controlled DocuTrust benchmark using the same project and a
  20ms artificial delay per provider call:

```text
serial baseline      4.701s, 200 calls, 208 generated, 1 skipped
two workers          2.456s, 200 calls, 208 generated, 1 skipped
two workers repeat   2.426s, 200 calls, 208 generated, 1 skipped
```

No real-provider scan was run.

## 2026-10-21 — scan no-visible-response diagnosis and fix plan

The exact command was observed against `D:\\SISTEC\\DocuTrust` with a bounded
12-second run and no real generation allowed to continue. The process produced
no stdout or stderr before the observation timeout. The project has no local
`codewhy.toml`, so the default Ollama provider was used. Ollama was running, but
`GET http://localhost:11434/api/version` did not respond within a 2-second
probe. The CLI currently prints no progress between provider construction and
`target.preflight()`. Ollama preflight makes two sequential requests, each with
the 10-second preflight cap, so the blank terminal is the result of blocking in
preflight before discovery and before the first pipeline item.

Plan:

1. Emit one immediate, quiet-aware provider-preflight progress line before
   consent/preflight, without reading project files or changing the fail-fast
   ordering.
2. Add CLI regression tests for visible preflight progress, quiet suppression,
   and preflight failure before source reads.
3. Run focused provider/CLI/pipeline/concurrency tests and the full offline
   suite; do not run real-provider generation.

### Implementation and verification

- Added a quiet-aware `checking provider <endpoint>...` line immediately after
  provider construction and before consent/preflight. The architecture's
  fail-fast ordering is unchanged: no discovery, extraction, or source read
  occurs before preflight succeeds.
- **Passed** — focused scan/provider/pipeline/concurrency tests: 100 passed.
- **Passed** — bounded reproduction of the exact DocuTrust command: after 12
  seconds, the unresponsive preflight still had not completed, but the command
  had already displayed `checking provider http://localhost:11434...`.
- **Passed** — full offline suite: 649 passed, 5 skipped, 1 existing Windows
  symlink-privilege warning.
- **Not run (explicitly avoided)** — real provider generation and full
  DocuTrust scan; the local Ollama preflight was observed only under a bounded
  timeout and was stopped before generation.

## 2026-10-21 — qwen2.5-coder:14b investigation and fix plan

The requested model remains `qwen2.5-coder:14b`. Bounded local probes showed
that `/api/tags` lists the model and a later `/api/version` probe completed in
about 2.1 seconds. CodeWhy preflight only checks the server/version/model list;
it does not load or execute the model. Therefore the observed generation hang
cannot be attributed to model availability from preflight alone. The supplied
hardware and direct Ollama observations are consistent with model loading or
inference pressure, especially because the current Task 6 change opts Ollama
into two concurrent generation requests.

Plan:

1. Add a separate validated `llm.preflight_timeout_seconds` setting with a
   conservative default and document it in the architecture/templates.
2. Make Ollama preflight timeout errors explicit and include the configured
   duration; make generation timeout errors explicit while preserving retries.
3. Remove Ollama's concurrency opt-in so large local models are never loaded or
   evaluated concurrently; keep bounded concurrency for OpenAI-compatible
   providers and preserve deterministic ordering.
4. Add offline tests for timeout values/messages and the Ollama serial-safety
   decision, then run focused and full regression suites without model calls.

### Implementation and verification

- Added `llm.preflight_timeout_seconds` with a default of 10 seconds to the
  validated config, templates, and architecture config contract. Generation
  continues to use `llm.timeout_seconds` (default 300 seconds).
- Timeout messages now distinguish preflight from generation and include the
  configured duration. Retries and existing error classification remain
  unchanged.
- Removed Ollama's concurrency opt-in. The pipeline remains bounded and
  concurrent for OpenAI-compatible providers, while local Ollama requests are
  serial to avoid two large model/KV-cache allocations on a 16 GB machine.
- **Passed** — focused configuration/provider/scan/pipeline tests: 167 passed.
- **Passed** — full offline suite: 653 passed, 5 skipped, 1 existing Windows
  symlink-privilege warning.
- **Passed** — `git diff --check`.
- **Passed** — bounded live preflight for the requested
  `qwen2.5-coder:14b`: the model was listed by `/api/tags`; CodeWhy preflight
  passed in 2.09 seconds with a 3-second configured preflight timeout.
- **Observed** — `ollama list` reports the 14B model at 9.0 GB, while
  `/api/ps` showed only the 7B model resident. The 14B generation path was not
  invoked because it can be expensive and was reported to hang. CodeWhy-side
  request and timeout fixes are verified, but successful 14B inference on this
  16 GB host remains unverified and may be limited by model plus runtime memory
  requirements.

## 2026-10-21 — qwen2.5-coder:14b blocking-path follow-up

The current code path after successful preflight is:
`run_pipeline` → `run_file_pass` → `call_pass` →
`OllamaProvider.generate_json` → non-streaming `/api/chat`. Ollama is asked to
return one complete JSON response, and the CLI reports an item only after that
request returns. The configured generation timeout is finite, but the CLI did
not have an explicit `KeyboardInterrupt` boundary; retry/backoff can also make
the wait appear indefinite. The fix will verify timeout values on actual
HTTPX requests, convert Ctrl+C into a clean exit, and keep output unwritten
until the existing render/write stage.

### Follow-up implementation and verification

- Added a CLI `KeyboardInterrupt` boundary: scan exits 130 with
  `scan interrupted; no output was written.` and still closes the provider.
- Added offline tests that inspect HTTPX request timeout extensions for both
  preflight and generation, plus the clean interrupt/no-write path.
- **Passed** — focused hang-path tests: 59 passed.
- **Passed** — full offline suite: 654 passed, 5 skipped, 1 existing Windows
  symlink-privilege warning.
- **Passed** — `git diff --check`.
- The full DocuTrust scan was not rerun because it would invoke the real 14B
  model and write generated documentation into the user project. The reported
  14B load/inference behavior therefore remains a hardware/model-dependent
  limitation, not a claimed end-to-end success.

## 2026-10-21 — scan stage timing instrumentation

- Added optional, non-sensitive stage callbacks to `build_project` for
  discovery, extraction, and graph construction.
- Added optional stage callbacks to `run_pipeline` for file, module, and
  project generation. Existing item progress and deterministic result ordering
  are unchanged.
- The CLI now prints stage start/completion messages and elapsed durations
  unless `--quiet` is used. Messages contain only stage names and aggregate
  counts; they never print source text, prompts, model output, or secrets.
- **Passed** — focused stage/provider/timeout tests: 60 passed.
- **Passed** — full offline suite: 655 passed, 5 skipped, 1 existing Windows
  symlink-privilege warning.
- **Passed** — `git diff --check`.
- **Not run (not authorized/appropriate for offline regression)** — the full
  real-model DocuTrust scan. The requested 14B model remains unchanged.

## 2026-10-21 — individual generation request timing

- Added safe request-level progress around each provider attempt. Messages
  include the project-relative subject, attempt number, estimated prompt token
  count, output-token cap, and completion/failure status. Source text, prompts,
  model responses, and secrets are never printed.
- Added request callbacks through file, module, and project passes without
  changing prompt construction, validation, retry behavior, or output order.
- Offline measurement of the first non-empty DocuTrust file found a roughly
  610-token prompt against a 13,824-token prompt budget, with a 1,024-token
  output cap and 16,384-token context. This is not an oversized prompt; the
  remaining delay is consistent with CPU-bound 14B inference on the reported
  hardware.
-
## 2026-10-21 - architecture-contract cleanup and final regression

- Restored `docs/ARCHITECTURE.md` byte-for-byte to the repository version; no
  architecture contract was changed.
- Removed the previously added undocumented `preflight_timeout_seconds`
  configuration field from configuration examples, templates, validation, and
  providers. Preflight retains the existing fixed fail-fast timeout; generation
  continues to use the configured `timeout_seconds`.
- **Passed** - focused provider/config/scan/pipeline regression tests: 168
  passed.
- **Passed** - full offline suite: 654 passed, 5 skipped, 1 existing Windows
  symlink-privilege warning.
- **Passed** - `git diff --check`.
- **Not run** - real `D:\Game\Breathing-game` or 14B generation. The measured
  single JavaScript prompt fits the configured budget, so CPU-bound Ollama
  inference remains a hardware/model limitation rather than an oversized
  request or a claimed end-to-end success.
- **Passed** — focused request-progress/provider/pipeline tests: 91 passed.
- **Passed** — full offline suite: 655 passed, 5 skipped, 1 existing Windows
  symlink-privilege warning.
- **Passed** — `git diff --check`.
- **Not run** — real 14B generation; no end-to-end model success is claimed.
