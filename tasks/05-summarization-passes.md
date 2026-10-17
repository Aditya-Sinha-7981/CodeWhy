# codewhy Task Specification

> **This file is a work order for the assigned developer and their AI coding agent.**
>
> This task defines the scope of the assigned work. Do not expand the scope without explicit approval from the project lead.

---

## Task Information

**Task:** `Summarization passes: prompts, file/module/project passes, chunking, overflow ladders, retries, orchestrator`

**Task ID:** `CW-05` (index: `tasks/ALL-TASKS.md`)

**Depends on:** CW-01, CW-02, CW-03, CW-04

**Branch:** `task/05-passes` (suggested; create only when the user wants a branch)

**Priority:** `Critical` (this is the product's value)

**Status:** `Not Started`

**Work log:** `logs/05-summarization-passes.md`

---

# 1. Goal

## Objective

Implement Layer 4: turn a `ProjectModel` plus a `Provider` into validated file, module, and project summaries. Deliverables:

1. **Prompt templates** with `PROMPT_VERSION` and a deterministic structure-to-text renderer.
2. **`file_pass`** with the empty-file shortcut, the fit rule, and AST-boundary **chunking** for oversized files.
3. **`module_pass`** and **`project_pass`** with deterministic **overflow ladders**.
4. **Retry-once-with-error** for malformed output; **`[generation failed]`** handling.
5. **`PassResult`** per the architecture, and an **orchestrator** (`pipeline.py`) that runs file → module → project in a fixed order through a single `run_pass` seam (the cache plugs in there in task 07).
6. **The budget invariant** enforced and property-tested.
7. A dev script to run the passes on a real project and print JSON.

No rendering, no cache, no `scan` wiring.

## Why This Exists

This is the part users judge the tool by. Its design rules decide quality: the model is asked only "why" and "how they fit" (never what the names are), it is told who imports the file so "why this exists" is answerable, and no prompt may ever exceed the context budget. The ladders and chunking make that last rule true by construction.

---

# 2. Authoritative Documentation

* `CLAUDE.md`
* `docs/ARCHITECTURE.md` — *Layer 4* (documentation contract, pass schemas, provider interface, prompt rules, context budgeting, `file_pass`, chunking, overflow ladders, retries and failure), *Layer 5 — reserved*, *Failure boundaries*
* `docs/TESTS.md` — *Budgeting and prompt assembly*, *Layer 4 — Summarization*, *Chunking*, *Layer 5*, fixture `oversized_file`

---

# 3. Scope

## In Scope

* `src/codewhy/summarize/{prompts,file_pass,module_pass,project_pass,result}.py`, `src/codewhy/llm/chunking.py`, `src/codewhy/pipeline.py`, `src/codewhy/text.py` (`first_sentence`, shared small text helpers).
* `build_oversized_file` in `tests/fixtures/builders.py`.
* `scripts/dev_run_passes.py`.
* Tests in §8.

## Out of Scope

* Rendering Markdown, frontmatter, output files (task 06).
* The cache and `input_key` (task 07). Build the `run_pass` seam only.
* `scan` wiring, dry-run, progress UI (tasks 06/08).
* Layer 5 grounding. Return `metadata == {}`.
* Parallel/async execution: passes run sequentially in the fixed order below.
* Any new dependency.

Do not implement "while we're here" improvements outside this scope.

---

# 4. Allowed Files / Areas

```text
src/codewhy/summarize/prompts.py
src/codewhy/summarize/result.py
src/codewhy/summarize/file_pass.py
src/codewhy/summarize/module_pass.py
src/codewhy/summarize/project_pass.py
src/codewhy/llm/chunking.py
src/codewhy/pipeline.py
src/codewhy/text.py
tests/fixtures/builders.py                    (add build_oversized_file)
scripts/dev_run_passes.py
tests/test_prompts.py
tests/test_file_pass.py
tests/test_chunking.py
tests/test_module_pass.py
tests/test_project_pass.py
tests/test_retries.py
tests/test_pipeline.py
tests/test_budget_invariant.py
logs/05-summarization-passes.md
```

### May Modify If Required

```text
docs/ARCHITECTURE.md   (contract details this task forces — logged)
src/codewhy/summarize/schemas.py   (field descriptions only)
```

---

# 5. Dependencies

| Dependency | Type | Notes |
|---|---|---|
| CW-02 `FileStructure` (`source_text`, `top_level_units`), CW-03 graph helpers + `entry_point`, CW-04 `Provider`/`FakeProvider`/schemas/budget | Hard | |
| A working Ollama or Gemini setup | Environment | Only for the live check; tests use `FakeProvider`. |

### Dependency Status

* [x] Dependency exists and is ready
* [ ] Dependency is being developed separately
* [x] Dependency requires coordination (live model for the quality check)
* [ ] No external dependency

---

# 6. Contracts That Must Be Preserved

### Architecture

* **Invariant:** every prompt any pass sends satisfies `estimate_tokens(prompt) <= prompt_budget`. Enforced by the ladders/chunking below, never by hope.
* LLM output is structured JSON validated by the pass pydantic model. The renderer (task 06) owns Markdown; passes never produce headings.
* Structural facts (function names, signatures, dependency lists, entry point) are **never** requested from the model; they are rendered deterministically later. The prompts ask for *why* and *how they fit together*.
* Every pass prompt contains verbatim: *"Do not invent behavior, APIs, or functionality not evidenced in the provided structure or source. If intent cannot be confidently established, say so plainly rather than fabricating a plausible explanation."*
* `file_pass` prompt contains the file's `imports` and `imported_by` **paths only** — never neighbor summaries (so neighbor edits do not cascade).
* Full source is included whenever the assembled prompt fits the budget; there is **no line-count threshold**.
* Project prompt with `entry_point is None` must explicitly instruct the model to state that no conventional entry point was detected and never to guess one; with an entry point it contains that path.
* Module/project prompt inputs are Purpose / Responsibility **text only**, never full summaries (Notes, `how_pieces_cooperate`, `architecture` are not inputs).
* Retry policy: malformed output (unparseable JSON, schema-validation failure, empty required string) → retry **once** with the validation error appended; second failure → `[generation failed]`. Transient provider errors are already retried inside the provider; if they surface as `TransientError`, the result is `[generation failed]` with no further retry. `AuthError` and `BadRequestError` abort the **whole run** (they are configuration problems, not per-file failures) by propagating.
* `PassResult(data, metadata, generated_at, failure)`; `data is None` iff `failure` is set; `metadata == {}`.

### Data conventions

* Deterministic processing order: files sorted by path, then modules sorted by path, then the project pass. All text assembled into prompts is built from sorted inputs.

### Safety

* Never log prompts, source, or model output above debug level.

---

# 7. Implementation Requirements

### Requirement 1 — Result type (`summarize/result.py`)

```python
@dataclass(frozen=True)
class PassResult:
    data: BaseModel | None
    metadata: dict            # always {} in v1
    generated_at: datetime
    failure: str | None
```

`PassResult.ok(data, now)`, `PassResult.failed(reason, now)`. `now` comes from an injectable `clock: Callable[[], datetime]` (UTC, timezone-aware) so tests are deterministic.

### Requirement 2 — Text helpers (`text.py`)

`first_sentence(text: str, max_chars: int = 300) -> str`: split on `(?<=[.!?])\s+`, take the first piece, collapse internal whitespace; if longer than `max_chars`, cut at `max_chars - 1` and append `…`. Deterministic, never empty for non-empty input.

### Requirement 3 — Prompts (`summarize/prompts.py`)

`PROMPT_VERSION = 1` (bump by hand whenever any template or schema changes; task 07 keys the cache on it).

Renderers (pure, deterministic):

* `render_structure(fs: FileStructure) -> str`: compact text listing of `classes` (name, bases, methods with signatures), `functions` (signatures), `types`, each with its docstring's first sentence if present. Sorted as in the source order by `start_line`.
* `file_prompt(fs, imports: list[str], imported_by: list[str], *, include_source: bool) -> str`
* `chunk_prompt(fs, chunk: Chunk, all_names: list[str], imports, imported_by) -> str` (a chunk summary request that includes the file's full list of top-level names for global context)
* `combine_prompt(fs, chunk_summaries: list[str], imports, imported_by) -> str`
* `module_prompt(module_path, files: list[tuple[str, str]], edges: str, external: list[str]) -> str`
* `project_prompt(root_name, entry_point: str | None, modules: list[tuple[str, str]], module_edges: str, other_files: list[str]) -> str` — `other_files` capped at 50 paths with `… and N more`.
* `retry_suffix(error: str) -> str`: `"\n\nYour previous response was invalid: <error>. Reply with only a JSON object matching the schema."`

Every prompt states, in this order: the role (a careful engineer documenting a codebase for a reader new to it, who may paste the result into a chat later); the verbatim no-invention rule; the output format ("JSON object only, plain prose strings, no headings, no Markdown lists, no code fences"); the task; the inputs. The file prompt asks specifically *why this file exists in this project given what imports it and what it imports*, written in plain language; the module prompt asks what the folder is responsible for and how its files cooperate; the project prompt asks what the project is, and how control/data flows between modules, grounded only in what is provided. Schema field descriptions (from `schemas.py`) are included in the prompt so the instruction travels with non-schema-enforcing providers.

### Requirement 4 — `file_pass` (`summarize/file_pass.py`)

`run_file_pass(fs, imports, imported_by, *, provider, config, clock) -> PassResult`

1. **Empty-file shortcut:** no non-blank, non-comment lines (comment = Python `#…`, JS/TS `//…` or entirely inside `/* */`) → `PassResult.ok(FilePass(purpose="Empty file."), …)` with **no** provider call. A skipped (`status != "ok"`) file never reaches this function.
2. **Fit rule:** assemble `file_prompt(include_source=True)`; if `fits`, one call. Else go to chunking.
3. **Chunking (`llm/chunking.py`):** `plan_chunks(fs, budget, overhead_tokens) -> list[Chunk]`:
   * Units come from `fs.top_level_units`. Chunk budget = `prompt_budget − estimate_tokens(chunk template with the file's names list, imports, imported_by)`; if that leaves less than a minimum useful size (e.g. 500 estimated tokens), the file fails with `failure="context too small for this file; raise llm.context_tokens"`.
   * Greedily pack consecutive units into chunks whose source slice (by `start_line..end_line`, 1-based inclusive) fits the chunk budget.
   * A **class** unit that alone exceeds the chunk budget is split at method boundaries: first chunk gets the class header lines up to the first method, then each method (children units) is packed greedily; the header attaches to the first chunk.
   * A **single function/method** that alone exceeds the chunk budget is **truncated**: keep the head (signature, docstring, then as many body lines as fit) followed by a line `[...truncated, N lines omitted]` where N is the exact number of omitted source lines. The file does not fail for this.
   * Every unit/line range appears in exactly one chunk; chunk source slices are contiguous and in order.
4. Per chunk: one `chunk_prompt` call returning a `FilePass`-shaped dict; keep only `purpose` text. Then **exactly one** `combine_prompt` call producing the file-level `FilePass`. If the combine prompt would exceed the budget, first reduce each chunk summary to `first_sentence`.
5. Chunk calls are not individually cached (task 07 caches only the final file result). Chunk-level failures: if any chunk's pass ends `failed` after the retry policy, the whole file result is `failed("chunk N failed: …")` (do not combine partial results).

### Requirement 5 — `module_pass` (`summarize/module_pass.py`)

`run_module_pass(module, purposes: dict[str, str], edges_text: str, external: list[str], in_degree: dict[str, int], *, provider, config, clock)`. `purposes` maps each member file to its Purpose text, or the placeholder `"(summary unavailable)"` for failed files.

**Ladder** (stop at the first step whose assembled prompt fits):
1. Full Purpose texts for all files.
2. `first_sentence` of each.
3. Include as many files as fit ordered by in-degree desc (ties by path) and append the line `N more files omitted from this prompt` with the exact count. (In-degree for a file = `len(imported_by)`.)

The module's relevant-edge text and external dependency names are part of the prompt; if even step 3 cannot fit them, truncate the edge list line by line (sorted order) with an `… and N more edges` line. The **final, post-ladder input object** is what task 07 hashes into the cache key, so return it alongside the result (`ModuleRun(result, prompt_inputs)`).

### Requirement 6 — `project_pass` (`summarize/project_pass.py`)

Same ladder over module Responsibility texts (`module_in_degree` from task 03 orders step 3), inputs: root name, entry point (or `None`), module list with Responsibility, a module-to-module edge summary derived from file edges (`pkg/a -> pkg/b` lines, deduped, sorted), and the capped other-file list. Returns `ProjectRun(result, prompt_inputs)`.

### Requirement 7 — Retry and failure (`summarize/` shared helper)

`call_pass(provider, prompt, model_cls, *, max_output_tokens, temperature=None) -> BaseModel`: calls `provider.generate_json(prompt, provider_schema(model_cls), …)`, validates with the pydantic model. On `MalformedOutput` or `ValidationError`, retries **once** with `prompt + retry_suffix(<error text, ≤ 500 chars>)` and `temperature=RETRY_TEMPERATURE` (module constant `0.2`); second failure raises `PassFailed(reason)` which the pass wrapper converts to a failed `PassResult`. `TransientError` → failed result immediately. `AuthError`/`BadRequestError` propagate.

### Requirement 8 — Orchestrator (`pipeline.py`)

```python
@dataclass
class RunResult:
    files: dict[str, PassResult]; modules: dict[str, PassResult]; project: PassResult
    counts: dict[str, int]   # generated, cached, failed, skipped
def run_pipeline(project: ProjectModel, provider: Provider, config: Config, *, clock, on_item: Callable[[str, str, str], None] | None = None) -> RunResult
def run_pass(pass_name: str, subject: str, inputs: object, fn: Callable[[], PassResult]) -> PassResult   # v1 of this task: just calls fn()
```

* Order: files (sorted), modules (sorted, root `.` first), project last. Skipped files (`status == "skipped"`) are not summarized and are counted in `counts["skipped"]`.
* All three passes go through `run_pass`; that is the **only** seam task 07 will modify.
* `on_item(pass_name, subject, status)` is called after each item with status `generated` or `failed` (task 07 adds `cached`).
* Failed file Purpose text is the placeholder `"(summary unavailable)"` in module prompts. A failed module's Responsibility likewise in the project prompt. A fully failed project pass is a failed `PassResult`, not an exception.

### Requirement 9 — Dev script

`scripts/dev_run_passes.py PATH [--provider P] [--model M]`: builds the project, runs `run_pipeline` with the real provider, prints each result as JSON, the number of calls, and estimated tokens per prompt. For manual quality inspection of prompts and outputs on real projects. It must refuse to run against a remote provider without `--yes`.

### Requirement 10 — Fixture builder

`build_oversized_file(tmp_path, *, chunk_tokens: int)`: a Python file whose pieces scale with `chunk_tokens`: many medium functions summing to several chunks; one class larger than a chunk (header + several methods); one single function larger than a chunk; plus ordinary imports. Returns the project root.

### Error Handling

* A model giving garbage never aborts a run: it becomes `[generation failed]` for that item (retry once first).
* Configuration problems (`AuthError`, `BadRequestError`) abort immediately with the provider's message; partial results are not discarded by this task (task 06/07 decide what is written).
* `context too small` is a per-file failure with an actionable reason, not a crash.

---

# 8. Testing Requirements

## Automated Tests

(All with `FakeProvider`.)

* `test_prompts.py`: verbatim no-invention rule in every prompt; ordering of sections; `file_prompt` contains `imports`/`imported_by` paths and **no neighbor summaries**; project prompt with and without entry point contains the required instruction/path; `other_files` cap and `… and N more`; schema descriptions present; `render_structure` deterministic and source-ordered.
* `test_file_pass.py`: empty-file shortcut (no calls; also comment-only files); full source included when it fits (a 400-line file that fits **does** get source); recorded prompt for the oversized path contains no full-source prompt; failed-chunk → failed file.
* `test_chunking.py` (using `build_oversized_file`): split points only on unit boundaries (property test: no chunk starts/ends mid-unit); every chunk prompt fits the budget; contiguous, in-order, full coverage; each chunk prompt includes the file's top-level names; class split at method boundaries with header on the first chunk; single oversized function truncated with the exact `[...truncated, N lines omitted]` count and the file still succeeds; exactly one combine call after the chunk calls; combine overflow → chunk summaries cut to first sentences; `context too small` failure path.
* `test_module_pass.py` / `test_project_pass.py`: ladder step 1→2→3 transitions with the exact `N more … omitted` counts and in-degree ordering; the returned `prompt_inputs` reflect the post-ladder text; placeholder for failed members.
* `test_retries.py`: unparseable JSON, schema-invalid JSON, empty required string each trigger exactly one retry whose prompt contains the error text and uses `RETRY_TEMPERATURE`; second failure → failed result and the run continues; `TransientError` → failed without retry; `AuthError`/`BadRequestError` propagate.
* `test_pipeline.py`: order (files sorted → modules root-first → project); skipped files excluded and counted; every pass goes through `run_pass` (monkeypatch it to count); failed file placeholder reaches the module prompt; `on_item` statuses; `metadata == {}` everywhere (**Layer 5 placeholder test**); deterministic results with `fixed_clock`.
* `test_budget_invariant.py`: **property test** over `flat_scripts`, `nested_modules`, `python_js_mixed`, `oversized_file`, with several `context_tokens` values including a deliberately tiny one: every prompt recorded by the `BudgetCheckingFakeProvider` satisfies the budget. Tiny budgets must yield `context too small` failures, never an over-budget prompt.

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected result:

```text
all tests pass offline; the budget invariant holds on every fixture and context size
```

## Hardware / Model-Dependent Checks

Record each as Passed / Failed / Not run (reason) in `logs/05-summarization-passes.md`:

* `scripts/dev_run_passes.py` on `tests/fixtures/flat_scripts` with real Ollama and/or Gemini. Include the JSON output (it contains explanations of the fixture code only).
* The same on one of your own real projects (do not paste its code or outputs into the log if private; record counts, failures, and your qualitative assessment).
* A forced-chunking run: set a small `llm.context_tokens` so the oversized fixture chunks with a real model; confirm the combined purpose is coherent.

## Manual Verification

1. Read the real model's file Purposes for `flat_scripts`: do they explain *why* each file exists (who uses it), not just *what* functions it contains? If not, iterate on the prompts here, bump `PROMPT_VERSION`, and log what changed and why.
2. Confirm a model that returns prose with fences or a missing field is handled (use a temporary stub to simulate) without crashing the run.
3. Count provider calls for a 15-file project; confirm it equals files + modules + 1 (+ chunk/combine/retry calls).

---

# 9. Acceptance Criteria

* [ ] File, module, and project passes produce validated `PassResult`s per §7 on all fixtures.
* [ ] The budget invariant holds in the property test at every context size.
* [ ] Chunking handles files, oversized classes, and oversized single functions per the locked fallback; exactly one combine call.
* [ ] Overflow ladders behave per §7 with correct omitted counts.
* [ ] Retry-once-with-error and `[generation failed]` behave per §6; auth/config errors abort the run.
* [ ] Every pass runs through `run_pass`; the orchestrator order is deterministic.
* [ ] Real-model output for `flat_scripts` explains purpose, not just content, or prompt iterations are logged.
* [ ] `PROMPT_VERSION` exists and the log says how to bump it.
* [ ] No new dependencies; no prompts/source/output logged above debug.
* [ ] `git diff` reviewed; work log updated.
* [ ] If a commit was authorized: staged diff reviewed, only intended files, no AI attribution, no push/PR.

---

# 10. Known Risks

* **Prompt quality is the product** and cannot be unit-tested. A prompt that makes a 14B model list function names instead of explaining purpose will pass every test here and still fail users. The manual check in §8 and task 09's evaluation are the real gates.
* **`estimate_tokens` is approximate.** A real tokenizer may count more for dense code; the 0.9 factor and the conservative `len/3` are the safety margin. If live runs show truncation, tune the factor and document it.
* **Chunk boundaries and meaning.** Splitting a class at methods loses cross-method context; the names list and the single combine call are the only mitigation. Do not add more chunk machinery.
* **Failed items cascade into placeholders.** A model that fails on many files yields thin module and project text; make sure the placeholder does not read as a real summary downstream (task 06 renders it visibly).
* **Sequential execution is slow** for local models on big projects. Do not parallelize here; task 07's resume-on-crash and task 08's progress output are the mitigation.
* **Gemini-style reasoning models** may fail the retry path more often if reasoning eats output tokens (see task 04's finding); keep the retry's token handling consistent with that finding.

---

# 11. Open Questions

* **requires clarification:** Should the combine step for chunked files see the chunk summaries' `notes` too? — Recommended: no (Purpose text only), matching the cascade rule.
* **requires clarification:** Minimum useful chunk size (500 estimated tokens in this spec) — keep or tune after live runs? — Recommended: keep; revisit only if real projects hit it.

---

# 12. Handoff Notes

* Task 06 renders `PassResult.data` fields and the `[generation failed]` placeholder, and wires `run_pipeline` into `scan` (with consent and preflight from task 04).
* Task 07 modifies `run_pass` only, computing `input_key` from the inputs described in the architecture: file (path, `source_text`, imports, imported_by), module (`prompt_inputs` post-ladder + edges + externals), project (`prompt_inputs` post-ladder + entry point + edges + other-file list as sent). Include `PROMPT_VERSION`.
* Task 08 builds `--dry-run` on top of prompt assembly without calling the provider; keep prompt assembly separable from calling.
* Record in the log: prompt iterations and `PROMPT_VERSION` history, observed real-model failure rates, token estimate accuracy notes, and architecture edits.

If the task is complete:

> No outstanding handoff notes.

---

# 13. Definition of Done

See `CLAUDE.md` → *Definition of done*. Do not commit, push, open a PR, or merge unless the user explicitly authorizes that specific action.

## Final Agent Instruction

Before making changes: read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, this file, and the logs of tasks 01–04; run `git status`; write your plan into `logs/05-summarization-passes.md`.

During implementation: keep prompt assembly separable from provider calls; never exceed the budget; stay in scope; do not invent architecture; test incrementally.

If the task requires something not covered here, stop and ask the project lead.

Before handing off: run the tests, run the live checks you can (record the rest as Not run), review `git diff` and `git status`, update the log, and report what changed, what was verified, and what is untested or unresolved.
